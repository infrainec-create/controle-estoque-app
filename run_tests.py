import os
import sys
import unittest
import pandas as pd
from datetime import datetime

# --- CONFIGURAÇÃO DO AMBIENTE DE TESTE ---
# Redireciona o banco de dados para um arquivo temporário de teste
import database.connection
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estoque_teste.db")
database.connection.DB_PATH = TEST_DB_PATH

# Garante que qualquer arquivo temporário anterior seja removido
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)
for suffix in ["-wal", "-shm"]:
    extra_file = TEST_DB_PATH + suffix
    if os.path.exists(extra_file):
        os.remove(extra_file)

# Importa os módulos do sistema para testar após a alteração do DB_PATH
from database.schema import init_db
from database.queries import (
    listar_produtos,
    listar_movimentacoes,
    cadastrar_produto,
    editar_produto,
    deletar_produto,
    registrar_log_auditoria
)
from utils.security import gerar_hash_senha
from utils.reports import (
    gerar_excel_estoque,
    gerar_excel_movimentacoes,
    gerar_excel_auditoria,
    gerar_html_pdf_estoque
)

import streamlit as st

class TestWMSRegression(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # 1. Inicializa o banco de dados de teste
        print("-> Inicializando banco de dados de teste...")
        init_db()
        st.cache_data.clear()
        
    @classmethod
    def tearDownClass(cls):
        # Remove os arquivos de teste criados após a conclusão
        print("\n-> Limpando arquivos de teste...")
        st.cache_data.clear()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        for suffix in ["-wal", "-shm"]:
            extra_file = TEST_DB_PATH + suffix
            if os.path.exists(extra_file):
                try: os.remove(extra_file)
                except: pass

    def test_01_security_hash(self):
        print("Teste 1: Validando criptografia de senhas (PBKDF2 com Salt)...")
        senha = "senha_secreta_123"
        hash_1 = gerar_hash_senha(senha)
        hash_2 = gerar_hash_senha(senha)
        
        self.assertNotEqual(hash_1, hash_2, "Hashes gerados para a mesma senha devem ser diferentes devido ao salt aleatório.")
        self.assertTrue(hash_1.startswith("pbkdf2_sha256$"), "O hash gerado deve seguir o formato PBKDF2.")
        self.assertNotEqual(senha, hash_1, "A senha em texto puro não deve ser igual ao hash.")
        
        # Teste de verificação compatível
        from utils.security import verificar_senha
        self.assertTrue(verificar_senha(senha, hash_1), "O hash deve ser verificado com sucesso.")
        self.assertFalse(verificar_senha("outra_senha", hash_1), "Uma senha incorreta deve falhar na verificação.")
        
        # Teste de retrocompatibilidade com SHA-256 legado
        import hashlib
        legacy_hash = hashlib.sha256(senha.encode()).hexdigest()
        self.assertTrue(verificar_senha(senha, legacy_hash), "A verificação deve aceitar hashes SHA-256 legados.")

    def test_02_database_crud(self):
        print("Teste 2: Validando operações de CRUD no Banco de Dados...")
        
        # A. Cadastrar
        sucesso, msg = cadastrar_produto(
            nome="Insumo Teste Regressao",
            estoque_minimo=15,
            valor_unitario=4.50,
            categoria="Geral",
            lead_time=5
        )
        self.assertTrue(sucesso, f"Falha ao cadastrar produto: {msg}")
        st.cache_data.clear()
        
        # B. Listar e validar inserção
        df_produtos = listar_produtos()
        self.assertFalse(df_produtos.empty, "A tabela de produtos não deveria estar vazia.")
        
        produto_inserido = df_produtos[df_produtos["nome"] == "Insumo Teste Regressao"]
        self.assertEqual(len(produto_inserido), 1, "O produto cadastrado deveria ser encontrado.")
        id_produto = int(produto_inserido.iloc[0]["id"])
        
        # C. Editar
        sucesso_edit, msg_edit = editar_produto(
            id_p=id_produto,
            nome="Insumo Teste Editado",
            min_e=20,
            valor=5.50,
            cat="EPI",
            lead=7
        )
        self.assertTrue(sucesso_edit, f"Falha ao editar produto: {msg_edit}")
        st.cache_data.clear()
        
        df_produtos_atualizado = listar_produtos()
        prod_editado = df_produtos_atualizado[df_produtos_atualizado["id"] == id_produto].iloc[0]
        self.assertEqual(prod_editado["nome"], "Insumo Teste Editado")
        self.assertEqual(int(prod_editado["estoque_minimo"]), 20)
        self.assertEqual(float(prod_editado["valor_unitario"]), 5.50)
        self.assertEqual(prod_editado["categoria"], "EPI")
        self.assertEqual(int(prod_editado["lead_time"]), 7)
        
        # D. Deletar
        sucesso_del, msg_del = deletar_produto(id_produto)
        self.assertTrue(sucesso_del, f"Falha ao deletar produto: {msg_del}")
        st.cache_data.clear()
        
        df_produtos_final = listar_produtos()
        prod_deletado = df_produtos_final[df_produtos_final["id"] == id_produto]
        self.assertTrue(prod_deletado.empty, "O produto deveria ter sido excluído com sucesso.")

    def test_03_audit_logging(self):
        print("Teste 3: Validando registro e rastreabilidade de Logs de Auditoria...")
        sucesso = registrar_log_auditoria(
            usuario="admin_teste",
            acao="Teste Integracao",
            detalhes="Verificando se o log de auditoria persiste corretamente no banco de dados."
        )
        self.assertTrue(sucesso, "O registro de auditoria falhou.")
        
        with database.connection.get_conn() as conn:
            logs = pd.read_sql("SELECT * FROM logs_auditoria WHERE usuario='admin_teste'", conn)
        
        self.assertEqual(len(logs), 1, "Deveria existir exatamente um registro de log para o usuário admin_teste.")
        self.assertEqual(logs.iloc[0]["acao"], "Teste Integracao")
        self.assertEqual(logs.iloc[0]["detalhes"], "Verificando se o log de auditoria persiste corretamente no banco de dados.")

    def test_04_reports_generation(self):
        print("Teste 4: Validando geração de Planilhas Excel (.xlsx) e Relatórios HTML/PDF...")
        
        # Cria dados fictícios estruturados para teste
        df_produtos_dummy = pd.DataFrame([
            {"id": 1, "nome": "Papel A4", "saldo_atual": 100, "estoque_minimo": 10, "valor_unitario": 25.0, "categoria": "Escritório", "lead_time": 3},
            {"id": 2, "nome": "Luvas Látex", "saldo_atual": 50, "estoque_minimo": 20, "valor_unitario": 15.0, "categoria": "EPI", "lead_time": 5}
        ])
        
        df_movimentacoes_dummy = pd.DataFrame([
            {"id": 1, "produto": "Papel A4", "data_hora": "31/05/2026 21:00", "tipo": "Entrada", "quantidade": 100, "saldo_resultante": 100, "observacao": "Lote inicial Pago: R$ 25.00/un"},
            {"id": 2, "produto": "Luvas Látex", "data_hora": "31/05/2026 21:05", "tipo": "Entrada", "quantidade": 50, "saldo_resultante": 50, "observacao": "Lote inicial Pago: R$ 15.00/un"}
        ])
        
        df_logs_dummy = pd.DataFrame([
            {"id": 1, "usuario": "admin", "acao": "Teste", "data_hora": "31/05/2026 21:00", "detalhes": "Log de teste"}
        ])
        
        # A. Excel de Estoque (Valuation)
        excel_estoque = gerar_excel_estoque(df_produtos_dummy)
        self.assertGreater(len(excel_estoque), 0, "O Excel de Estoque não deveria estar vazio.")
        self.assertEqual(excel_estoque[:2], b'PK', "Assinatura ZIP (PK) inválida para arquivo OpenXML (.xlsx).")
        
        # B. Excel de Movimentações
        excel_movs = gerar_excel_movimentacoes(df_movimentacoes_dummy)
        self.assertGreater(len(excel_movs), 0, "O Excel de Movimentações não deveria estar vazio.")
        self.assertEqual(excel_movs[:2], b'PK', "Assinatura ZIP (PK) inválida para arquivo OpenXML (.xlsx).")
        
        # C. Excel de Auditoria
        excel_auditoria = gerar_excel_auditoria(df_logs_dummy)
        self.assertGreater(len(excel_auditoria), 0, "O Excel de Auditoria não deveria estar vazio.")
        self.assertEqual(excel_auditoria[:2], b'PK', "Assinatura ZIP (PK) inválida para arquivo OpenXML (.xlsx).")
        
        # D. Relatório HTML/PDF
        html_pdf = gerar_html_pdf_estoque(df_produtos_dummy, df_movimentacoes_dummy, df_logs_dummy)
        self.assertGreater(len(html_pdf), 0, "O HTML de visualização não deveria estar vazio.")
        self.assertIn("Relatório Executivo WMS 5.0", html_pdf, "O título correto do relatório deveria estar contido no HTML.")

if __name__ == "__main__":
    print("======================================================================")
    print(" INICIANDO BATERIA DE TESTES DE REGRESSÃO DE INTEGRAÇÃO - WMS 5.0 ")
    print("======================================================================")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWMSRegression)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Retorna o código de saída correto dependendo do sucesso dos testes
    sys.exit(0 if result.wasSuccessful() else 1)
