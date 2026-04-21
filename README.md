# Everest TaxDeed Workers

Sistema automatizado de inteligência em tax deed sales para 11 condados da Flórida.

**100% grátis** rodando em GitHub Actions + Supabase + Cowork.

---

## Arquitetura

```
GitHub Actions (cron)  -->  Workers Python  -->  SQLite / Supabase
       |                                              |
       +-- PDF semanal ----> email via SMTP           +-- Planilha atualizada
       +-- Lot scraping --> popula DB
       +-- Calendar scrape --> agenda futuros sales
```

**Workers inclusos:**
- `calendar_scraper` — descobre datas dos próximos sales (11 condados)
- `lot_list_scraper` — baixa lotes de um sale (Lee como piloto, generico para os demais)
- `spreadsheet_writer` — atualiza a planilha Excel com os lotes
- `gerar_pdf_semanal` — gera PDF A4 do calendário semanal
- `enviar_email_pdf` — envia PDF por email via SMTP

**Condado piloto:** Lee (terças semanais, 10:00 AM ET, plataforma RealAuction).

---

## Deploy em 10 passos (zero custo, ~30 min)

### 1. Criar repo no GitHub
Vá em https://github.com/new, crie repo **privado** chamado `everest-taxdeed`. Não inicialize com nada.

### 2. Push do código local
No Windows, abra PowerShell em `C:\Users\dpr20\OneDrive\Área de Trabalho\Everest Investments Documents\Florida\everest-taxdeed-workers`:

```bash
git init
git add .
git commit -m "Initial commit: everest-taxdeed-workers"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/everest-taxdeed.git
git push -u origin main
```

### 3. Criar Supabase (já está fazendo)
- Projeto: `everest-taxdeed`
- Region: East US (North Virginia)
- Salve a senha do DB.
- Em **Settings → API**: copie **Project URL** e **anon public key**.

### 4. Adicionar secrets no GitHub repo
Vá em **Settings → Secrets and variables → Actions → New repository secret** e adicione:

| Secret | Valor |
|---|---|
| `SUPABASE_URL` | `https://xxxxx.supabase.co` |
| `SUPABASE_KEY` | a anon public key |
| `SMTP_HOST` | `smtp.gmail.com` (se usar Gmail) ou outro |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | seu email remetente |
| `SMTP_PASSWORD` | senha de app (Gmail → App Passwords) |

**Para usar Gmail para SMTP:** habilite 2FA na sua conta Google → https://myaccount.google.com/apppasswords → gera senha de app → usa essa senha em `SMTP_PASSWORD`.

### 5. Rodar schema no Supabase (opcional — se já quiser usar Postgres)
Supabase → SQL Editor → Cole o conteúdo de `src/db/schema.sql` → Run.

> **Alternativa mais simples:** por enquanto o sistema usa SQLite local (commitado no repo). Supabase entra em uma segunda iteração.

### 6. Habilitar Actions
GitHub → Actions → clique **"I understand my workflows..."** para habilitar.

### 7. Primeiro run manual
Actions → Calendar Scraper → **Run workflow** → Branch main → Run.

Aguarde 1-2 min. Status verde = tudo rodando.

### 8. Verificar outputs
Após o primeiro run:
- `data/taxdeed.db` commitado com os 11 condados + sales agendados
- `logs/` disponível como artifact no Actions

### 9. Rodar Lot Scraper piloto
Actions → Lot List Scraper → Run workflow → County: `LEE` → Run.

### 10. Primeiro PDF semanal
Actions → PDF Semanal → Run workflow → Run.

PDF fica em `data/outputs/` e é enviado para `dpr2004@hotmail.com` automaticamente.

---

## Executar localmente (teste antes do deploy)

```bash
cd everest-taxdeed-workers
python -m venv .venv
source .venv/bin/activate   # ou .venv\Scripts\activate no Windows
pip install -r requirements.txt

# Copia a planilha pra pasta data/
cp "../Planilha_Padrao_11Condados_Everest.xlsx" data/

# Inicializa DB + seeds
python -m src.main --init

# Calendar scraper (popula sales)
python -m src.main --scrape-calendar

# Lot scraper piloto (Lee)
python -m src.main --scrape-lots --county LEE

# Atualiza planilha
python -m src.main --update-spreadsheet --county LEE

# Gera PDF semanal
python scripts/gerar_pdf_semanal.py
```

---

## Cronograma automático (já configurado)

| Worker | Quando | Custo Actions |
|---|---|---|
| Calendar Scraper | Diário 6:00 AM ET | ~1 min/dia |
| Lot List Scraper | Diário 6:30 AM ET | ~3 min/dia |
| PDF Semanal + Email | Toda segunda 7:00 AM ET | ~2 min/semana |
| **Total/mês** | | **~130 min** (de 2000 grátis) |

Sobram 1870 min/mês para expansão.

---

## Como expandir (outros condados e estados)

O sistema foi desenhado pra escalar. Adicionar novos condados é barato.

### Adicionar 1 condado novo (interativo)

```bash
python scripts/adicionar_condado.py
# Preenche nome, estado, URL, cadência
# Depois:
python -m src.main --init   # re-seed (idempotente)
```

### Adicionar múltiplos condados de uma vez (batch)

Crie um arquivo `novos_condados.json`:

```json
{
  "condados": [
    {
      "codigo": "MIAMI_DADE",
      "estado": "FL",
      "nome": "Miami-Dade County",
      "aba_planilha": "MIAMI_DADE",
      "cadencia": { "tipo": "WEEKLY", "dia_semana": 2 },
      "horario_et": "09:00 AM ET",
      "plataforma": "RealTDA",
      "url_sales": "https://www.miamidade.realtaxdeed.com/",
      "url_clerk": "https://www2.miamidadeclerk.gov/taxdeed/",
      "telefone": "(305) 275-1155",
      "deposito": "5% ou $200",
      "status": "CONFIRMADO"
    }
  ]
}
```

Depois:

```bash
python scripts/adicionar_condado.py --file novos_condados.json
python -m src.main --init
```

### Campo `estado` por condado

Toda entrada em `config/condados.json` tem `"estado": "FL"`. Para Texas use `"TX"`, Arizona `"AZ"`, etc. O DB já tem coluna `state` em `counties`.

### Custos em escala (free tier)

| Qtde condados | Min/mês GitHub Actions | Storage Supabase | Status |
|---|---|---|---|
| 11 (atual) | ~130 | ~1 MB | Dentro free tier |
| 67 (FL inteiro) | ~250 | ~5 MB | Dentro free tier |
| 300 (FL + 3 estados) | ~800 | ~30 MB | Dentro free tier |
| 1000+ | ~2500 | ~100 MB | Considera upgrade GitHub Pro ($4/mês) |

### Parsers específicos por plataforma

Alguns condados usam plataformas próprias (não RealAuction). Para adicionar suporte:

1. Em `src/workers/lot_list_scraper.py`, crie método `_parse_<codigo>` (ex.: `_parse_maricopa`).
2. Implemente a extração de lotes específica.
3. O scraper chama automaticamente se o método existir.

Plataformas já mapeadas: RealAuction, RealTaxDeed, RealTDA, RealForeclose.
Plataformas futuras para mapear: govease (AL/MS), ParcelFair (AZ), Bid4Assets (CA/TX).

---

## Próximos passos (fases futuras)

- [ ] Migrar de SQLite para Supabase Postgres (muda `src/db/connection.py`)
- [ ] Adicionar Property Appraiser worker (enriquecimento dados)
- [ ] Adicionar FEMA flood zone checker
- [ ] Adicionar URL generator (Zillow/Redfin/Street View)
- [ ] Scoring engine (roda após cada update)
- [ ] Alert engine (Slack/email quando score > 200)
- [ ] Dashboard web Next.js (conecta no Supabase)
- [ ] Integração com time de agentes de viabilidade (webhook)

---

## Troubleshooting

**Actions falhou:** Abra o run, clique no step vermelho, leia log. Comum: `tenacity` ou outra dep faltando → adiciona em `requirements.txt`.

**Scraper retorna zero lotes:** Site do condado pode ser JS-heavy. Solução: descomentar `playwright>=1.40.0` em `requirements.txt` e adaptar o parser.

**Email não chega:** Verificar Gmail App Password (não senha normal), 2FA habilitado, `ALERT_EMAIL_TO` correto.

**DB cresce muito:** SQLite no repo incha com tempo. Quando passar de 10 MB, migrar pra Supabase (já preparado).

---

## Contato
Daniel Rocha — dpr2004@hotmail.com — Everest Investments — Coconut Creek, FL
