import os
import sqlite3

# Definição absoluta do caminho do banco para evitar duplicidades
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "estoque.db")

def get_conn():
    # timeout=30.0 força a conexão a aguardar se o banco estiver ocupado
    return sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
