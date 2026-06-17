import os
import sqlite3
import time

from contextlib import contextmanager

# Definição absoluta do caminho do banco para evitar duplicidades
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "estoque.db")

@contextmanager
def get_conn():
    # timeout=30.0 força a conexão a aguardar se o banco estiver ocupado
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    # Habilita a checagem e integridade de chaves estrangeiras (Foreign Keys)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def retry_db_operation(max_retries=3, delay=0.5):
    """
    Decorador para tentar novamente operações no banco de dados caso ocorra
    um travamento de escrita concorrente (OperationalError: database is locked).
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        last_err = e
                        time.sleep(delay * (2 ** attempt)) # exponential backoff
                    else:
                        raise e
            raise last_err
        return wrapper
    return decorator
