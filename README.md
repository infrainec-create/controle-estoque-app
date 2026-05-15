# 📦 Sistema de Controle de Estoque

App de estoque com Streamlit + SQLite. Funciona direto no Chromebook via Linux (Crostini).

---

## Instalação (passo a passo)

### 1. Abrir o terminal Linux no Chromebook

Vá em **Configurações → Avançado → Ambiente de desenvolvimento Linux** e ative o Linux.

### 2. Instalar as dependências

```bash
pip install streamlit pandas --break-system-packages
```

> Se der erro, tente: `pip3 install streamlit pandas`

### 3. Rodar o app

Coloque os arquivos `app.py` e `requirements.txt` em uma pasta (ex: `~/estoque`) e rode:

```bash
cd ~/estoque
streamlit run app.py
```

O app vai abrir automaticamente no navegador em `http://localhost:8501`.

---

## Funcionalidades

| Aba | O que faz |
|---|---|
| 📊 Painel | Visão geral com métricas e lista de produtos |
| ⬇️ Entrada | Registra recebimento de mercadoria |
| ⬆️ Saída | Registra baixa ou envio |
| 🔧 Ajuste | Corrige saldo manualmente (avaria, perda) |
| 📋 Contagem Semanal | Você informa o físico, o app calcula o consumo |
| 📜 Histórico | Todas as movimentações com filtro por tipo |
| ➕ Cadastrar Produto | Adiciona novos produtos ao sistema |

---

## Lógica da contagem semanal

```
Consumo = Saldo Sistêmico − Estoque Físico Contado
Novo Saldo = Estoque Físico Contado
```

O sistema registra automaticamente uma movimentação do tipo **"Contagem"** com o consumo calculado.

---

## Banco de dados

O SQLite cria o arquivo `estoque.db` automaticamente na mesma pasta do `app.py`.
Para fazer backup, basta copiar esse arquivo.

Para abrir o banco e fazer consultas SQL diretamente:

```bash
sqlite3 estoque.db
```

Consultas úteis:

```sql
-- Ver todos os produtos
SELECT * FROM produtos;

-- Ver movimentações da semana
SELECT p.nome, m.tipo, m.quantidade, m.saldo_resultante, m.data_hora
FROM movimentacoes m
JOIN produtos p ON p.id = m.id_produto
ORDER BY m.id DESC;

-- Consumo total por produto (contagens semanais)
SELECT p.nome, SUM(ABS(m.quantidade)) AS consumo_total
FROM movimentacoes m
JOIN produtos p ON p.id = m.id_produto
WHERE m.tipo = 'Contagem'
GROUP BY p.nome;
```
