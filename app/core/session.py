"""会话管理 - 用户隔离，只保留问题不保留回答"""
import time
import threading
from typing import Optional, Dict, List


class SessionManager:
    """内存会话管理器，按 conversation_id 隔离用户"""

    def __init__(self, ttl: int = 1800, max_questions: int = 5):
        self._sessions: Dict[str, Dict] = {}
        self._ttl = ttl           # 30分钟过期
        self._max_questions = max_questions  # 最多保留5轮问题
        self._lock = threading.Lock()

    def get(self, conversation_id: str) -> Optional[Dict]:
        if not conversation_id:
            return None
        with self._lock:
            session = self._sessions.get(conversation_id)
            if not session:
                return None
            if time.time() - session["last_active"] > self._ttl:
                del self._sessions[conversation_id]
                return None
            return session

    def update(self, conversation_id: str, coin_symbol: str = None, question: str = None):
        if not conversation_id:
            return
        with self._lock:
            if conversation_id not in self._sessions:
                self._sessions[conversation_id] = {
                    "coin_symbol": None,
                    "questions": [],
                    "last_active": 0,
                }
            session = self._sessions[conversation_id]
            if coin_symbol:
                session["coin_symbol"] = coin_symbol
            if question:
                session["questions"].append(question)
                if len(session["questions"]) > self._max_questions:
                    session["questions"] = session["questions"][-self._max_questions:]
            session["last_active"] = time.time()

    def cleanup(self):
        """清理过期会话"""
        with self._lock:
            now = time.time()
            expired = [k for k, v in self._sessions.items()
                       if now - v["last_active"] > self._ttl]
            for k in expired:
                del self._sessions[k]


# 全局单例
session_manager = SessionManager()
