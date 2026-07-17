import os
import glob
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

def realizar_backup_local():
    """
    Cria uma cópia física do banco de dados atual na pasta backups/
    e mantém apenas os 5 backups mais recentes para otimizar espaço em disco.
    """
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"estoque_backup_{timestamp}.db"
        dest_path = os.path.join(BACKUP_DIR, backup_filename)
        
        # Cria cópia física segura usando SQLite Online Backup API
        import sqlite3
        from database.connection import get_conn
        with get_conn() as conn_src:
            with sqlite3.connect(dest_path) as conn_dst:
                conn_src.backup(conn_dst)
        
        # Gerenciamento rotativo: listar todos os backups e deletar os mais antigos se passar de 5
        backups_existentes = sorted(glob.glob(os.path.join(BACKUP_DIR, "estoque_backup_*.db")))
        if len(backups_existentes) > 5:
            for b in backups_existentes[:-5]:
                try:
                    os.remove(b)
                except Exception:
                    pass
                    
        return True, dest_path
    except Exception as e:
        return False, str(e)
