# 📦 Controle de Estoque Inteligente (Cloud & AI)

Um sistema de gestão de insumos e controle logístico desenvolvido em **Python (Streamlit)**, concebido para operações de alto giro e ambientes de *fulfillment*. O aplicativo transforma o registro operacional básico num *pipeline* de dados automatizado, fornecendo inteligência de negócio e gestão visual para evitar rupturas de estoque.

## 🚀 Funcionalidades Principais

* **Gestão à Vista (Semáforo):** Classificação visual automática (🔴 Crítico, 🟡 Atenção, 🟢 Saudável) baseada no cruzamento entre o saldo atual e o estoque mínimo.
* **Inteligência Logística (Curva ABC):** Classificação automática do peso financeiro de cada insumo no inventário, permitindo focar auditorias nos itens de Classe A.
* **Cálculo de WMS (Lead Time):** Sugestões de compras dinâmicas baseadas no consumo médio e no tempo de entrega (Lead Time) do fornecedor.
* **Assistente de IA Integrado:** Conexão direta com a API do **Google Gemini**, atuando como um analista virtual que audita os dados e gera relatórios estratégicos de reposição.
* **Pipeline ETL em Nuvem:** Sincronização em tempo real do banco de dados SQLite com o Google Drive. A cada movimentação, o sistema exporta arquivos `.csv` limpos, prontos para consumo em *dashboards* do Looker Studio ou *queries* no BigQuery.

## 🛠️ Tecnologias Utilizadas

* **Python 3**
* **Streamlit:** Interface de usuário ágil e responsiva.
* **Pandas & SQLite3:** Manipulação de dados e banco de dados relacional.
* **Google Drive API:** Armazenamento em nuvem via Conta de Serviço (Service Account).
* **Google Generative AI:** Geração de insights via modelo Gemini.

## ⚙️ Como Executar Localmente

1. Clone este repositório:
   ```bash
   git clone [https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git](https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git)