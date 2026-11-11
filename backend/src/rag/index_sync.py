# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 知识库索引跨进程同步（Redis Pub/Sub）
==================================================================
背景：
    后端 uvicorn 以多 worker（--workers 2）运行，BM25 索引是
    每进程独立的内存对象；Chroma 向量库虽是共享磁盘文件，
    但重建会删除并新建集合（UUID 变化），其他进程的旧集合句柄
    会失效（vector_store 已做失效自愈）。

职责：
    1. publish_kb_index_changed：知识库文档增/删/改/重建成功后，
       向 Redis 频道广播索引变更事件；
    2. start_kb_sync_listener：每个 worker 启动时开启后台监听线程，
       收到事件即从 MySQL 全量重建本进程的 BM25 内存索引
       （毫秒级，无需重嵌入），保证各 worker 检索结果一致。

可靠性：
    - Redis 不可用时静默降级（发布/监听均不抛错，知识库功能不受影响，
      依靠进程重启时的 ensure_bootstrapped 兜底恢复）；
    - 监听线程 daemon 化，随进程退出；断线 5 秒自动重连。
==================================================================
"""
import logging
import threading
import time

from src import database

logger = logging.getLogger("zhifatong.kb.sync")

CHANNEL = "zhifatong:kb:index_changed"   # 索引变更广播频道

_stop = threading.Event()
_thread: threading.Thread | None = None


def publish_kb_index_changed() -> None:
    """
    广播知识库索引变更事件（文档增/删/改/全量重建后调用）。

    Redis 不可用时静默跳过（多进程一致性降级，单进程内仍一致）。
    """
    try:
        if database.redis_client is None:
            return
        database.redis_client.publish(CHANNEL, str(int(time.time() * 1000)))
        logger.info("已广播知识库索引变更事件")
    except Exception as e:
        logger.debug("索引变更广播失败（降级运行）: %s", e)


def _rebuild_bm25_locally() -> None:
    """重建本进程的 BM25 内存索引（从 MySQL 全量，毫秒级）。"""
    # 延迟导入避免循环依赖（knowledge_service -> 向量/BM25 模块）
    from src.rag.knowledge_service import knowledge_service
    n = knowledge_service.rebuild_bm25_from_db()
    logger.info("跨进程索引同步：本进程 BM25 已按库内文档重建（%d 块）", n)


def _listen_loop() -> None:
    """监听线程主循环：断线自动重连，收到事件即重建本地 BM25。"""
    while not _stop.is_set():
        try:
            if database.redis_client is None:
                _stop.wait(5)
                continue
            pubsub = database.redis_client.pubsub(
                ignore_subscribe_messages=True)
            pubsub.subscribe(CHANNEL)
            logger.info("知识库索引同步监听已启动（频道 %s）", CHANNEL)
            while not _stop.is_set():
                msg = pubsub.get_message(timeout=1.0)
                if msg and msg.get("type") == "message":
                    try:
                        _rebuild_bm25_locally()
                    except Exception as e:
                        logger.warning("本地 BM25 重建失败: %s", e)
            pubsub.close()
        except Exception as e:
            logger.warning("索引同步监听断开，5s 后重连: %s", e)
            if _stop.wait(5):
                break


def start_kb_sync_listener() -> None:
    """启动索引同步监听线程（幂等；每个 worker 各自启动一份）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    if database.redis_client is None:
        logger.info("Redis 不可用，跳过知识库索引同步监听（降级运行）")
        return
    _stop.clear()
    _thread = threading.Thread(target=_listen_loop,
                               name="kb-index-sync", daemon=True)
    _thread.start()


def stop_kb_sync_listener() -> None:
    """停止监听线程（应用 shutdown 时调用）。"""
    _stop.set()
