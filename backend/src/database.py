# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 数据库访问层
==================================================================
模块职责：
    1. MySQL 连接池管理与通用查询/执行助手（DBUtils 风格，简化实现）；
    2. Redis 客户端（缓存 / 登录防爆破计数）；
    3. 连接健康探测接口（启动自检用）。

设计要点：
    - pymysql + 手写简易连接池：避免额外依赖，满足单机 QPS 要求；
    - 每次借出连接前 ping 检测，杜绝 "MySQL server has gone away"；
    - 查询结果统一转为 dict 列表，便于业务层直接使用。
==================================================================
"""
import logging
import threading
import time
from contextlib import contextmanager

import pymysql
import redis

from src import config

logger = logging.getLogger("zhifatong.db")


# ==================================================================
# 一、MySQL 连接池
# ==================================================================
class MySQLPool:
    """
    MySQL 线程安全连接池。

    实现策略：
        - 初始化时预建 min_size 个连接，存放于线程安全的队列；
        - borrow()：取连接前 ping 保活，失效则重建；
        - release()：用完归还；损坏连接直接丢弃，由懒补充机制重建；
        - 池满时按需新建（不设上限，受 MySQL max_connections 约束）。
    """

    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str, pool_size: int = 8):
        self._params = dict(
            host=host, port=port, user=user, password=password,
            database=database, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,   # 结果转 dict
            autocommit=True,                          # 事务由调用方显式控制时再关闭
        )
        self._pool_size = pool_size
        self._pool: list[pymysql.connections.Connection] = []
        self._lock = threading.Lock()
        self._init_pool()

    def _new_conn(self):
        """创建一条新的 MySQL 连接。"""
        return pymysql.connect(**self._params)

    def _init_pool(self):
        """启动时预热连接池。"""
        for _ in range(2):
            try:
                self._pool.append(self._new_conn())
            except Exception as e:   # 数据库未就绪时不阻断导入
                logger.warning("MySQL 连接预热失败（稍后懒连接）: %s", e)
                break

    @contextmanager
    def connection(self):
        """
        借出连接的上下文管理器。

        用法:
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, args)
        """
        conn = self._borrow()
        try:
            yield conn
        finally:
            self._release(conn)

    def _borrow(self):
        """从池中取出一条可用连接（ping 保活，失败则重建）。"""
        with self._lock:
            conn = self._pool.pop() if self._pool else None
        if conn is None:
            return self._new_conn()
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return self._new_conn()

    def _release(self, conn):
        """归还连接到池中；池满则关闭。"""
        with self._lock:
            if len(self._pool) < self._pool_size:
                self._pool.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass

    def close_all(self):
        """关闭池内全部连接（进程退出时调用）。"""
        with self._lock:
            while self._pool:
                try:
                    self._pool.pop().close()
                except Exception:
                    pass


# 全局单例连接池
mysql_pool = MySQLPool(
    host=config.MYSQL_HOST, port=config.MYSQL_PORT,
    user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
    database=config.MYSQL_DATABASE, pool_size=config.MYSQL_POOL_SIZE,
)


def query_all(sql: str, args: tuple = ()) -> list[dict]:
    """执行查询，返回全部行（dict 列表）。"""
    with mysql_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()


def query_one(sql: str, args: tuple = ()) -> dict | None:
    """执行查询，返回单行（无结果返回 None）。"""
    rows = query_all(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    """
    执行写操作（INSERT/UPDATE/DELETE），返回受影响行数。
    写操作使用事务：失败自动回滚。
    """
    with mysql_pool.connection() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
            conn.commit()
            return cur.rowcount
        except Exception:
            conn.rollback()
            raise


def insert_return_id(sql: str, args: tuple = ()) -> int:
    """执行 INSERT 并返回自增主键 ID。"""
    with mysql_pool.connection() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
            conn.commit()
            return int(cur.lastrowid)
        except Exception:
            conn.rollback()
            raise


def ping() -> bool:
    """数据库连通性探测（SELECT 1）。"""
    try:
        return query_one("SELECT 1 AS ok") is not None
    except Exception as e:
        logger.error("MySQL ping 失败: %s", e)
        return False


# ==================================================================
# 二、Redis 客户端（缓存 / 防爆破计数 / 会话）
# ==================================================================
def _make_redis() -> redis.Redis | None:
    """创建 Redis 客户端；服务不可用时返回 None（系统降级运行）。"""
    try:
        client = redis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT,
            password=config.REDIS_PASSWORD or None,
            db=config.REDIS_DB, socket_connect_timeout=3,
            decode_responses=True,                   # 返回 str 而非 bytes
        )
        client.ping()
        return client
    except Exception as e:
        logger.warning("Redis 不可用，缓存功能降级: %s", e)
        return None


redis_client = _make_redis()


def redis_get(key: str) -> str | None:
    """读缓存（Redis 不可用时返回 None，业务自行回源）。"""
    if redis_client is None:
        return None
    try:
        return redis_client.get(key)
    except Exception:
        return None


def redis_set(key: str, value: str, ttl: int | None = None) -> None:
    """写缓存（带过期时间，秒）。"""
    if redis_client is None:
        return
    try:
        redis_client.set(key, value, ex=ttl)
    except Exception:
        pass


def redis_incr(key: str, ttl: int) -> int:
    """
    自增计数器（登录失败防爆破用）。

    参数:
        key: 计数键（如 login:fail:username）
        ttl: 首次创建时的过期时间（秒）

    返回:
        当前计数值；Redis 不可用返回 0（视为不限制）
    """
    if redis_client is None:
        return 0
    try:
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)   # 仅首次设置 TTL
        result = pipe.execute()
        return int(result[0])
    except Exception:
        return 0


def redis_del(key: str) -> None:
    """删除缓存键。"""
    if redis_client is None:
        return
    try:
        redis_client.delete(key)
    except Exception:
        pass
