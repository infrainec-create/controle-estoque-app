import os
import sqlite3
import threading
from datetime import datetime, timezone
from io import BytesIO
import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
from database.connection import get_conn, DB_PATH
from database.queries import limpar_cache_consultas
from utils.date_helpers import formatar_timestamp_utc

_sync_lock = threading.Lock()
_pending_sync = False

try:
    FOLDER_ID = st.secrets["FOLDER_ID"]
except Exception:
    FOLDER_ID = "MOCK_FOLDER_ID"


def _escape_drive_query_value(value):
    if value is None:
        return ""
    return str(value).replace("'", "''")


def _set_sync_status(sucesso, mensagem, chave='global'):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES (?, ?, ?, ?)",
                (chave, sucesso, mensagem, formatar_timestamp_utc())
            )
    except Exception:
        pass


def obter_servico_drive():
    if "gcp_service_account_custom" in st.session_state:
        info_chaves = dict(st.session_state["gcp_service_account_custom"])
    elif "gcp_service_account" in st.secrets:
        info_chaves = dict(st.secrets["gcp_service_account"])
    else:
        raise Exception("Credenciais do Google Cloud não encontradas no arquivo secrets.toml.")
        
    p_key = info_chaves.get("private_key", "")
    
    if info_chaves.get("project_id") == "seu-projeto-gcp" or "sua_chave_privada_aqui" in p_key:
        raise Exception("Credenciais de exemplo detectadas. Configure o secrets.toml com suas chaves reais do GCP para ativar a nuvem.")
        
    if "\\n" in p_key:
        info_chaves["private_key"] = p_key.replace("\\n", "\n")
        
    try:
        credenciais = service_account.Credentials.from_service_account_info(info_chaves)
        return build('drive', 'v3', credentials=credenciais)
    except Exception as e:
        raise Exception(f"Falha ao interpretar arquivo PEM: {e}")

def parsed_drive_time(iso_str):
    if not iso_str:
        return None
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(iso_str)
    except Exception:
        try:
            return datetime.strptime(iso_str.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

def obter_local_mtime():
    if os.path.exists(DB_PATH):
        mtime = os.path.getmtime(DB_PATH)
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    return None

def obter_metadados_drive():
    try:
        servico = obter_servico_drive()
        db_name = _escape_drive_query_value(os.path.basename(DB_PATH))
        folder_id = _escape_drive_query_value(FOLDER_ID)
        query = f"name='{db_name}' and '{folder_id}' in parents and trashed=false"
        res = servico.files().list(
            q=query, 
            fields="files(id, name, modifiedTime, size)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        files = res.get('files', [])
        if files:
            return files[0]
    except Exception:
        pass
    return None

def salvar_ultimo_sync_time_local(mtime_drive_str):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES ('ultimo_sync_drive_mtime', ?)",
                (mtime_drive_str,)
            )
    except Exception:
        pass

def obter_ultimo_sync_time_local():
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'ultimo_sync_drive_mtime'").fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return None

def sincronizar_banco_na_inicializacao():
    """
    Sincroniza o banco local com o banco do Drive no início da sessão.
    Se o banco do Drive for mais recente, realiza o download.
    """
    # 1. Se o banco não existir, estiver vazio, ou não tiver nenhum usuário cadastrado (banco local recém-criado sem sincronismo),
    # força a tentativa de download do Drive para recuperar as contas reais.
    has_users = False
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        try:
            with get_conn() as conn:
                res = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()
                if res and res[0] > 0:
                    has_users = True
        except Exception:
            pass

    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0 or not has_users:
        sucesso = descarregar_do_drive()
        if not sucesso:
            if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0 and has_users:
                # Se falhou ao baixar mas já temos um banco local com usuários válidos, continuamos offline
                return
            raise Exception("Não foi possível baixar o banco de dados do Google Drive e não há usuários cadastrados localmente.")
        return

    # Se já existe e tem tamanho, agora sim podemos ler configurações de sincronização
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'drive_sync_ativo'").fetchone()
            if row and row[0] == '0':
                return
    except Exception:
        pass

    try:
        meta_drive = obter_metadados_drive()
        if not meta_drive:
            return
            
        mtime_drive_str = meta_drive.get('modifiedTime')
        mtime_drive = parsed_drive_time(mtime_drive_str)
        
        mtime_local = obter_local_mtime()
        
        if mtime_drive and mtime_local:
            # Se o banco do Drive for mais recente por mais de 2 segundos, baixa
            if (mtime_drive - mtime_local).total_seconds() > 2:
                descarregar_do_drive()
    except Exception as e:
        _set_sync_status(0, f"Erro na sincronização de inicialização: {e}")

def executar_sincronizacao_drive():
    global _pending_sync
    if not _sync_lock.acquire(blocking=False):
        _pending_sync = True
        return
    try:
        while True:
            _pending_sync = False
            _executar_sincronizacao_drive_interna()
            if not _pending_sync:
                break
    finally:
        _sync_lock.release()

def _executar_sincronizacao_drive_interna():
    backup_db_path = DB_PATH + ".sync_backup"
    try:
        if os.path.exists(backup_db_path):
            try:
                os.remove(backup_db_path)
            except Exception:
                pass
                
        # Cria cópia estática consistente do DB usando SQLite Backup API
        with get_conn() as conn_src:
            try:
                conn_src.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass
            with sqlite3.connect(backup_db_path) as conn_dst:
                conn_src.backup(conn_dst)

        servico = obter_servico_drive()
        
        # 1. Verificação de Conflitos para evitar sobrescrever dados mais recentes da nuvem
        meta_drive = obter_metadados_drive()
        if meta_drive:
            mtime_drive_str = meta_drive.get('modifiedTime')
            ultimo_sync_local = obter_ultimo_sync_time_local()
            
            if ultimo_sync_local and mtime_drive_str != ultimo_sync_local:
                mtime_drive = parsed_drive_time(mtime_drive_str)
                mtime_local_sync = parsed_drive_time(ultimo_sync_local)
                
                if mtime_drive and mtime_local_sync and (mtime_drive - mtime_local_sync).total_seconds() > 2:
                    _set_sync_status(0, "Conflito detectado! O banco de dados na nuvem foi modificado por outra sessão. Recarregue a página ou faça download manual.")
                    return
        
        # 2. Upload Seguro do Banco de Dados (.db) com Retry (3 tentativas com Backoff)
        import time
        db_name = _escape_drive_query_value(os.path.basename(DB_PATH))
        folder_id = _escape_drive_query_value(FOLDER_ID)
        query = f"name='{db_name}' and '{folder_id}' in parents and trashed=false"
        
        upload_res = None
        for tentativa in range(3):
            try:
                files = servico.files().list(
                    q=query, 
                    fields="files(id)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute().get('files', [])
                
                media = MediaFileUpload(backup_db_path, mimetype='application/x-sqlite3', resumable=True)
                if files: 
                    upload_res = servico.files().update(
                        fileId=files[0]['id'], 
                        media_body=media,
                        fields="id, modifiedTime",
                        supportsAllDrives=True
                    ).execute()
                else: 
                    upload_res = servico.files().create(
                        body={'name': os.path.basename(DB_PATH), 'parents': [FOLDER_ID]}, 
                        media_body=media,
                        fields="id, modifiedTime",
                        supportsAllDrives=True
                    ).execute()
                break # Sucesso, sai do loop
            except Exception as e:
                if tentativa < 2:
                    time.sleep(2 ** tentativa)
                else:
                    raise e
            
        # Salva o novo timestamp de sincronização localmente
        if upload_res and 'modifiedTime' in upload_res:
            salvar_ultimo_sync_time_local(upload_res['modifiedTime'])
            
            # Criar cópia de backup rotativo no Drive usando slots pré-criados (evita cota 0 bytes da Conta de Serviço)
            try:
                # 1. Determinar o próximo slot de backup (1 a 5)
                ultimo_slot = 1
                try:
                    with get_conn() as conn:
                        row_slot = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'ultimo_backup_slot'").fetchone()
                        if row_slot:
                            ultimo_slot = int(row_slot[0])
                except Exception:
                    pass
                
                next_slot = (ultimo_slot % 5) + 1
                backup_name = f"estoque_backup_{next_slot}.db"
                
                # 2. Procurar se o arquivo do slot já existe na pasta do Drive
                backup_name_safe = _escape_drive_query_value(backup_name)
                folder_id = _escape_drive_query_value(FOLDER_ID)
                query_slot = f"name='{backup_name_safe}' and '{folder_id}' in parents and trashed=false"
                res_slot = servico.files().list(
                    q=query_slot,
                    fields="files(id)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True
                ).execute().get('files', [])
                
                if res_slot:
                    # Slot já existe (criado pelo usuário), então atualizamos ele (consumindo cota do usuário)
                    media_backup = MediaFileUpload(backup_db_path, mimetype='application/x-sqlite3', resumable=True)
                    servico.files().update(
                        fileId=res_slot[0]['id'],
                        media_body=media_backup,
                        supportsAllDrives=True
                    ).execute()
                    
                    # Salva o slot atualizado localmente
                    with get_conn() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES ('ultimo_backup_slot', ?)",
                            (str(next_slot),)
                        )
                else:
                    # Se não existe no Drive pessoal, tentamos criar.
                    # Se der erro de cota (o que acontece em contas de serviço sem shared drives), alertamos o usuário
                    try:
                        media_backup = MediaFileUpload(backup_db_path, mimetype='application/x-sqlite3', resumable=True)
                        servico.files().create(
                            body={'name': backup_name, 'parents': [FOLDER_ID]},
                            media_body=media_backup,
                            supportsAllDrives=True
                        ).execute()
                        
                        with get_conn() as conn:
                            conn.execute(
                                "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES ('ultimo_backup_slot', ?)",
                                (str(next_slot),)
                            )
                    except Exception as e_create:
                        if "storageQuotaExceeded" in str(e_create) or "quota" in str(e_create).lower():
                            with get_conn() as conn:
                                conn.execute(
                                    "INSERT OR REPLACE INTO status_sincronismo (chave, sucesso, mensagem, timestamp) VALUES ('backup_warning', 0, ?, ?)",
                                    (f"Aviso de Backup: Crie um arquivo vazio chamado '{backup_name}' no seu Google Drive pessoal para ativar este slot de backup.", formatar_timestamp_utc())
                                )
                        else:
                            raise e_create
            except Exception as e_backup:
                # Falhas na rotina de backup secundária não devem travar a sincronização principal
                pass
        
        # 3. Geração e Extração dos CSVs para o Looker Studio com Retry
        with get_conn() as conn:
            prods = pd.read_sql("SELECT * FROM produtos ORDER BY nome", conn)
            movs = pd.read_sql("""
                SELECT m.id, p.nome AS produto, m.data_hora, m.tipo, m.quantidade, m.saldo_resultante, m.observacao
                FROM movimentacoes m JOIN produtos p ON p.id = m.id_produto ORDER BY m.id DESC
            """, conn)
            
        for df, name in [(prods, "produtos_looker.csv"), (movs, "movimentacoes_looker.csv")]:
            file_name_safe = _escape_drive_query_value(name)
            folder_id = _escape_drive_query_value(FOLDER_ID)
            q = f"name='{file_name_safe}' and '{folder_id}' in parents"
            for tentativa in range(3):
                try:
                    fs = servico.files().list(
                        q=q,
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True
                    ).execute().get('files', [])
                    
                    m = MediaIoBaseUpload(BytesIO(df.to_csv(index=False).encode('utf-8-sig')), mimetype='text/csv')
                    if fs: 
                        servico.files().update(
                            fileId=fs[0]['id'], 
                            media_body=m,
                            supportsAllDrives=True
                        ).execute()
                    else: 
                        servico.files().create(
                            body={'name': name, 'parents': [FOLDER_ID]}, 
                            media_body=m,
                            supportsAllDrives=True
                        ).execute()
                    break # Sucesso
                except Exception as e:
                    if tentativa < 2:
                        time.sleep(2 ** tentativa)
                    else:
                        if "storageQuotaExceeded" in str(e) or "quota" in str(e).lower():
                            raise Exception(f"Cota Excedida. Por favor, crie um arquivo vazio chamado '{name}' na sua pasta do Drive para ativar a gravação.")
                        raise e
            
        _set_sync_status(1, "Nuvem sincronizada com sucesso!")
    except sqlite3.OperationalError as e:
        _set_sync_status(0, f"Banco de dados ocupado: {e}")
    except Exception as e:
        msg = str(e)
        if "storageQuotaExceeded" in msg or "do not have storage quota" in msg:
            msg = "Cota de armazenamento excedida. Contas de Serviço GCP não possuem cota própria no Drive pessoal. Crie arquivos em branco ou use um Drive Compartilhado."
        _set_sync_status(0, f"Erro de comunicação Drive: {msg}")
    finally:
        if os.path.exists(backup_db_path):
            try:
                os.remove(backup_db_path)
            except Exception:
                pass

def disparar_sincronizacao():
    limpar_cache_consultas()
    
    if st.session_state.get("db_sincronizado") == "local":
        _set_sync_status(1, "Modo Offline: Sincronização desativada.")
        return
        
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT valor FROM configuracoes WHERE chave = 'drive_sync_ativo'").fetchone()
            if row and row[0] == '0':
                _set_sync_status(1, "Sincronização na nuvem desativada localmente.")
                return
    except Exception:
        pass

    _set_sync_status(1, "Sincronizando em segundo plano...")
    threading.Thread(target=executar_sincronizacao_drive).start()

def descarregar_do_drive():
    try:
        servico = obter_servico_drive()
        db_name = _escape_drive_query_value(os.path.basename(DB_PATH))
        folder_id = _escape_drive_query_value(FOLDER_ID)
        query = f"name='{db_name}' and '{folder_id}' in parents and trashed=false"
        res = servico.files().list(
            q=query, 
            fields="files(id, modifiedTime)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        if res.get('files', []):
            file_meta = res['files'][0]
            req = servico.files().get_media(fileId=file_meta['id'])
            
            temp_path = DB_PATH + ".tmp"
            with open(temp_path, "wb") as f:
                load = MediaIoBaseDownload(f, req)
                done = False
                while not done: 
                    _, done = load.next_chunk()
            
            # Substituição segura a nível de conexão usando backup do SQLite
            # Isso evita ter que deletar ou renomear arquivos que possam estar em uso por outras conexões/consultas.
            with sqlite3.connect(temp_path) as conn_src:
                with get_conn() as conn_dst:
                    conn_src.backup(conn_dst)
                    # Força checkpoint no banco atualizado para limpar WAL e manter consistência
                    try:
                        conn_dst.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    except Exception:
                        pass
                        
            try:
                os.remove(temp_path)
            except Exception:
                pass
            
            # Remove arquivos WAL e SHM órfãos se existirem (embora o backup com checkpoint resolva)
            for suffix in ["-wal", "-shm"]:
                extra_file = DB_PATH + suffix
                if os.path.exists(extra_file):
                    try: os.remove(extra_file)
                    except Exception: pass
            
            salvar_ultimo_sync_time_local(file_meta.get('modifiedTime'))
            
            _set_sync_status(1, "Banco de dados baixado da nuvem com sucesso!")
            limpar_cache_consultas()
            return True
    except Exception as e:
        try:
            if os.path.exists(DB_PATH):
                _set_sync_status(0, f"Erro ao baixar da nuvem: {e}")
        except Exception:
            pass
        return False
    return False
