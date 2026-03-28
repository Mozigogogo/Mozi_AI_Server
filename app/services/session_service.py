"""会话管理服务 - 支持多用户会话隔离"""
import pymysql
from typing import List, Optional, Dict, Any
from collections import OrderedDict
from datetime import datetime, timedelta
from app.core.config import get_settings

settings = get_settings()


class LRUCache:
    """简单的LRU缓存实现"""

    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.cache: OrderedDict[str, List[tuple]] = OrderedDict()

    def get(self, key: str) -> Optional[List[tuple]]:
        """获取缓存值"""
        if key in self.cache:
            # 将访问的键移到末尾（最新访问）
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def put(self, key: str, value: List[tuple]):
        """设置缓存值"""
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value

        # 超过容量时删除最旧的项
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def delete(self, key: str):
        """删除缓存值"""
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        """清空缓存"""
        self.cache.clear()

    def __contains__(self, key: str) -> bool:
        return key in self.cache

    def __len__(self) -> int:
        return len(self.cache)


class SessionService:
    """会话管理服务 - 支持多用户会话隔离"""

    def __init__(self):
        self.settings = settings
        # LRU缓存：session_id -> List[(role, content)]
        self._cache = LRUCache(capacity=100)
        # 最大保留消息数（50轮=100条消息）
        self._max_messages = 100

    def get_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取会话历史（最多50轮=100条消息）

        Args:
            session_id: 会话ID
            limit: 最多获取的对话轮数（每轮包含user+assistant两条消息）

        Returns:
            会话历史消息列表 [{"role": "user", "content": "..."}, ...]
        """
        if not session_id:
            return []

        try:
            # 1. 先查缓存
            cached = self._cache.get(session_id)
            if cached is not None:
                messages = cached[-limit*2:]
                return [{"role": role, "content": content} for role, content in messages]

            # 2. 缓存未命中，查数据库
            messages = self._load_from_db(session_id, limit)
            # 缓存到内存
            self._cache.put(session_id, [(m["role"], m["content"]) for m in messages])
            return messages

        except Exception as e:
            # 数据库异常，返回空列表降级为无状态模式
            print(f"[SessionService] 获取会话历史失败: {e}")
            return []

    def add_message(self, session_id: str, role: str, content: str):
        """
        添加消息到会话历史

        Args:
            session_id: 会话ID
            role: 角色（user/assistant）
            content: 消息内容
        """
        if not session_id:
            return

        try:
            # 更新缓存
            cached = self._cache.get(session_id)
            if cached is None:
                cached = []
            cached.append((role, content))

            # 保持缓存中最多100条消息（50轮）
            if len(cached) > self._max_messages:
                cached = cached[-self._max_messages:]

            self._cache.put(session_id, cached)

            # 写入数据库
            self._save_to_db(session_id, role, content)

            # 清理数据库中的旧记录（只保留最新的50轮）
            self._cleanup_db(session_id, 50)

        except Exception as e:
            # 数据库写入失败，仅记录日志不中断服务
            print(f"[SessionService] 保存会话消息失败: {e}")

    def clear_session(self, session_id: str):
        """
        清除指定会话

        Args:
            session_id: 会话ID
        """
        if not session_id:
            return

        try:
            # 清除缓存
            self._cache.delete(session_id)
            # 删除数据库记录
            self._delete_from_db(session_id)
        except Exception as e:
            print(f"[SessionService] 清除会话失败: {e}")

    def clear_all(self):
        """清除所有会话缓存"""
        self._cache.clear()

    def _get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset=self.settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor
        )

    def _load_from_db(self, session_id: str, limit: int, time_limit_hours: int = 1) -> List[Dict[str, Any]]:
        """
        从数据库加载会话历史

        Args:
            session_id: 会话ID
            limit: 最多获取的对话轮数
            time_limit_hours: 时间限制（小时），只返回最近N小时内的消息，默认1小时

        Returns:
            会话历史消息列表
        """
        connection = None
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                # 检查表是否存在
                cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = 'session_history'
                """, (self.settings.mysql_database,))
                result = cursor.fetchone()

                if not result or result['cnt'] == 0:
                    # 表不存在，返回空列表
                    return []

                # 计算时间阈值（最近N小时）
                time_threshold = datetime.now() - timedelta(hours=time_limit_hours)

                # 查询历史消息 - 按时间过滤，同时限制数量
                sql = """
                    SELECT role, content, created_at
                    FROM session_history
                    WHERE session_id = %s AND created_at >= %s
                    ORDER BY created_at ASC
                    LIMIT %s
                """
                cursor.execute(sql, (session_id, time_threshold, limit * 2))
                rows = cursor.fetchall()

                messages = []
                for row in rows:
                    messages.append({
                        "role": row["role"],
                        "content": row["content"],
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None
                    })
                return messages

        finally:
            if connection:
                connection.close()

    def _save_to_db(self, session_id: str, role: str, content: str):
        """
        保存消息到数据库

        Args:
            session_id: 会话ID
            role: 角色
            content: 消息内容
        """
        connection = None
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                # 检查表是否存在
                cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = 'session_history'
                """, (self.settings.mysql_database,))
                result = cursor.fetchone()

                if not result or result['cnt'] == 0:
                    # 表不存在，创建表
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS session_history (
                            id BIGINT PRIMARY KEY AUTO_INCREMENT,
                            session_id VARCHAR(100) NOT NULL COMMENT '会话ID，由外层后端生成',
                            role VARCHAR(20) NOT NULL COMMENT '角色：user/assistant',
                            content TEXT NOT NULL COMMENT '消息内容',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                            INDEX idx_session_id (session_id),
                            INDEX idx_created_at (created_at)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话历史表'
                    """)

                # 插入消息
                sql = """
                    INSERT INTO session_history (session_id, role, content)
                    VALUES (%s, %s, %s)
                """
                cursor.execute(sql, (session_id, role, content))
                connection.commit()

        except Exception as e:
            if connection:
                connection.rollback()
            raise
        finally:
            if connection:
                connection.close()

    def _cleanup_db(self, session_id: str, limit: int, time_limit_hours: int = 1):
        """
        清理数据库中的旧记录

        清理策略：
        1. 删除超过N小时（默认1小时）的旧消息
        2. 如果某会话的消息数超过限制，删除最旧的记录

        Args:
            session_id: 会话ID
            limit: 保留的对话轮数（在时间限制内）
            time_limit_hours: 时间限制（小时），默认1小时
        """
        connection = None
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                # 1. 删除超过N小时的旧消息
                time_threshold = datetime.now() - timedelta(hours=time_limit_hours)
                sql = """
                    DELETE FROM session_history
                    WHERE session_id = %s AND created_at < %s
                """
                cursor.execute(sql, (session_id, time_threshold))
                connection.commit()

                # 2. 检查剩余消息数，如果仍超过限制，继续删除最旧的
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM session_history
                    WHERE session_id = %s
                """, (session_id,))
                result = cursor.fetchone()
                if not result:
                    return

                total = result['cnt']
                max_keep = limit * 2  # 转换为消息数

                if total > max_keep:
                    # 删除最旧的记录
                    sql = """
                        DELETE FROM session_history
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                        LIMIT %s
                    """
                    cursor.execute(sql, (session_id, total - max_keep))
                    connection.commit()

        finally:
            if connection:
                connection.close()

    def _delete_from_db(self, session_id: str):
        """
        删除数据库中的会话记录

        Args:
            session_id: 会话ID
        """
        connection = None
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                # 检查表是否存在
                cursor.execute("""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = 'session_history'
                """, (self.settings.mysql_database,))
                result = cursor.fetchone()

                if not result or result['cnt'] == 0:
                    return

                sql = "DELETE FROM session_history WHERE session_id = %s"
                cursor.execute(sql, (session_id,))
                connection.commit()

        finally:
            if connection:
                connection.close()


# 全局单例
session_service = SessionService()
