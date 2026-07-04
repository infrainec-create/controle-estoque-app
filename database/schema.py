import sqlite3
import random
from datetime import datetime, timedelta
from database.connection import get_conn

def init_db():
    with get_conn() as conn:
        # ATIVAÇÃO DO MODO WAL (Write-Ahead Logging) para concorrência segura
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessoes (
                token TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                data_criacao TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS status_sincronismo (
                chave TEXT PRIMARY KEY,
                sucesso INTEGER NOT NULL,
                mensagem TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                senha_hash TEXT NOT NULL,
                pergunta_seguranca TEXT NOT NULL,
                resposta_seguranca_hash TEXT NOT NULL,
                aprovado INTEGER DEFAULT 0,
                perfil TEXT DEFAULT 'Operador'
            );
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                saldo_atual INTEGER NOT NULL DEFAULT 0,
                estoque_minimo INTEGER DEFAULT 10,
                valor_unitario REAL DEFAULT 0,
                categoria TEXT DEFAULT 'Geral',
                lead_time INTEGER DEFAULT 3
            );
            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_produto INTEGER NOT NULL REFERENCES produtos(id),
                data_hora TEXT NOT NULL,
                tipo TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                saldo_resultante INTEGER NOT NULL,
                observacao TEXT
            );
            CREATE TABLE IF NOT EXISTS logs_auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                acao TEXT NOT NULL,
                data_hora TEXT NOT NULL,
                detalhes TEXT
            );
            CREATE TABLE IF NOT EXISTS configuracoes (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_movimentacoes_id_produto ON movimentacoes(id_produto);
            CREATE INDEX IF NOT EXISTS idx_movimentacoes_data_hora ON movimentacoes(data_hora);
            CREATE INDEX IF NOT EXISTS idx_logs_auditoria_usuario ON logs_auditoria(usuario);
            CREATE INDEX IF NOT EXISTS idx_logs_auditoria_data_hora ON logs_auditoria(data_hora);
        """)
        
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN aprovado INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'Operador'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE logs_auditoria ADD COLUMN ip TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE logs_auditoria ADD COLUMN user_agent TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("UPDATE usuarios SET perfil = 'Administrador' WHERE usuario = 'admin'")
        except Exception:
            pass
        try:
            conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('drive_sync_ativo', '1')")
            conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('crono_dias_antes_inicio_sol', '5')")
            conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('crono_dias_antes_fim_sol', '3')")
            conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('crono_dias_uteis_analise', '5')")
            conn.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('crono_dias_uteis_entrega', '3')")
        except Exception:
            pass
        
        # Seeding de dados iniciais caso a tabela de produtos esteja vazia para evitar telas em branco
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM produtos")
            if cursor.fetchone()[0] == 0:
                # 1. Cadastra os produtos iniciais
                produtos_semeadura = [
                    ("Papel A4 Premium", 15, 5, 25.00, "Escritório", 3),
                    ("Luvas de Látex Pro", 8, 10, 15.50, "EPI", 5),
                    ("Café Arábica 500g", 25, 8, 18.00, "Copa", 4),
                    ("Detergente Neutro 5L", 0, 3, 32.00, "Limpeza", 2)
                ]
                for nome_p, saldo_p, estoque_min_p, valor_p, cat_p, lead_p in produtos_semeadura:
                    conn.execute(
                        "INSERT INTO produtos (nome, saldo_atual, estoque_minimo, valor_unitario, categoria, lead_time) VALUES (?, ?, ?, ?, ?, ?)",
                        (nome_p, saldo_p, estoque_min_p, valor_p, cat_p, lead_p)
                    )
                
                # Obtém os IDs inseridos
                ids = {r[1]: r[0] for r in conn.execute("SELECT id, nome FROM produtos").fetchall()}
                
                # 2. Cadastra movimentações históricas fictícias para renderizar gráficos
                hoje_dt = datetime.now()
                
                for nome_p, id_prod in ids.items():
                    saldo_temp = 0
                    for i in range(5):
                        dias_atras = 25 - (i * 5)
                        data_mov = (hoje_dt - timedelta(days=dias_atras)).strftime("%d/%m/%Y %H:%M")
                        
                        # Entrada inicial
                        if i == 0:
                            qtd = 20
                            saldo_temp += qtd
                            conn.execute(
                                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Entrada', ?, ?, ?)",
                                (id_prod, data_mov, qtd, saldo_temp, f"Lote Inicial Almoxarifado | Pago: R$ {conn.execute('SELECT valor_unitario FROM produtos WHERE id=?', (id_prod,)).fetchone()[0]:.2f}/un")
                            )
                        # Saídas
                        elif i in [1, 3]:
                            qtd = random.randint(3, 5)
                            saldo_temp = max(0, saldo_temp - qtd)
                            conn.execute(
                                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Saída', ?, ?, ?)",
                                (id_prod, data_mov, -qtd, saldo_temp, "Consumo Interno Almoxarifado")
                            )
                        # Entrada de reposição
                        elif i == 2:
                            qtd = 10
                            saldo_temp += qtd
                            conn.execute(
                                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Entrada', ?, ?, ?)",
                                (id_prod, data_mov, qtd, saldo_temp, "Compra Almoxarifado de Urgência")
                            )
                        # Contagem/Auditoria
                        elif i == 4:
                            saldo_real = 15 if "Papel" in nome_p else (8 if "Luvas" in nome_p else (25 if "Café" in nome_p else 0))
                            diff = saldo_real - saldo_temp
                            conn.execute(
                                "INSERT INTO movimentacoes (id_produto, data_hora, tipo, quantidade, saldo_resultante, observacao) VALUES (?, ?, 'Contagem', ?, ?, ?)",
                                (id_prod, data_mov, diff, saldo_real, "Ajuste Auditoria Semanal Almoxarifado")
                            )
        except Exception:
            pass
