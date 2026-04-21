-- Schema SQLite - Everest TaxDeed System
-- Versao 1.0 - suporta migracao futura para Postgres/Supabase

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS counties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL DEFAULT 'FL',
    nome TEXT NOT NULL,
    aba_planilha TEXT NOT NULL,
    cadencia_tipo TEXT NOT NULL,       -- WEEKLY, ORDINAL, VERIFY
    cadencia_dia_semana INTEGER,       -- 0=seg, 1=ter, ..., 6=dom
    cadencia_ordem INTEGER,            -- para ORDINAL: 1, 2, 3, 4 (primeira, segunda... semana do mes)
    horario_et TEXT,
    plataforma TEXT,
    url_sales TEXT,
    url_clerk TEXT,
    url_property_appraiser TEXT,
    telefone TEXT,
    deposito TEXT,
    status TEXT,                       -- CONFIRMADO, PROJETADO, A_VERIFICAR
    ativo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    county_id INTEGER NOT NULL REFERENCES counties(id),
    sale_date DATE NOT NULL,
    sale_time TEXT,
    total_lots INTEGER,
    url_especifica TEXT,                -- URL do sale especifico (se diferente da URL master)
    status TEXT DEFAULT 'scheduled',    -- scheduled, in_progress, completed, cancelled
    scraped_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(county_id, sale_date)
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    tax_cert_num TEXT,
    case_num TEXT,
    parcel_id TEXT NOT NULL,
    address TEXT,
    city TEXT,
    zip TEXT,
    legal_description TEXT,
    property_type TEXT,                 -- SFR, Lot, Multi, Comm, Mobile, Outros
    lot_sqft REAL,
    building_sqft REAL,
    year_built INTEGER,
    bedrooms INTEGER,
    bathrooms REAL,
    zoning TEXT,
    min_bid REAL,
    assessed_value REAL,
    just_value REAL,
    homestead INTEGER DEFAULT 0,
    raw_data_json TEXT,                 -- payload bruto do scraping
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sale_id, parcel_id)
);

CREATE TABLE IF NOT EXISTS dd (
    lot_id INTEGER PRIMARY KEY REFERENCES lots(id),
    occupation_status TEXT,
    condition_score INTEGER,            -- 1-5
    code_violations TEXT,
    mortgage_status TEXT,
    iptu_due REAL,
    demolish_needed TEXT,
    water_sewer TEXT,
    accessibility TEXT,
    fema_flood_zone TEXT,
    fema_risk TEXT,
    street_view_url TEXT,
    zillow_url TEXT,
    redfin_url TEXT,
    confidence_score INTEGER,
    last_updated TIMESTAMP,
    dd_complete INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL REFERENCES lots(id),
    address TEXT,
    sale_date DATE,
    sale_price REAL,
    sqft REAL,
    distance_mi REAL,
    source TEXT,                        -- zillow, redfin, county
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS liens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL REFERENCES lots(id),
    lien_type TEXT,
    creditor TEXT,
    amount REAL,
    lien_date DATE,
    survives_taxdeed INTEGER,
    risk_score INTEGER,
    quiet_title_needed INTEGER,
    status TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scores (
    lot_id INTEGER PRIMARY KEY REFERENCES lots(id),
    max_bid_recommended REAL,
    projected_profit REAL,
    projected_roi REAL,
    final_score REAL,
    decision TEXT,                      -- LANCE, REVISAR, PASSA
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL REFERENCES lots(id),
    decision TEXT NOT NULL,
    max_bid REAL,
    justification TEXT,
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_by TEXT
);

CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    lot_id INTEGER NOT NULL REFERENCES lots(id),
    our_bid REAL,
    winning_bid REAL,
    won INTEGER,
    winner_name TEXT,
    bid_time TIMESTAMP
);

CREATE TABLE IF NOT EXISTS results (
    lot_id INTEGER PRIMARY KEY REFERENCES lots(id),
    actual_profit REAL,
    actual_roi REAL,
    hold_time_months INTEGER,
    exit_date DATE
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER REFERENCES sales(id),
    lesson_text TEXT NOT NULL,
    category TEXT,
    severity TEXT,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER REFERENCES lots(id),
    alert_type TEXT,
    message TEXT,
    severity TEXT,
    sent_to TEXT,
    sent_at TIMESTAMP,
    ack_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT,                        -- running, success, failed
    items_processed INTEGER,
    errors_count INTEGER,
    log_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_lots_sale ON lots(sale_id);
CREATE INDEX IF NOT EXISTS idx_lots_parcel ON lots(parcel_id);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_county ON sales(county_id);
CREATE INDEX IF NOT EXISTS idx_scores_decision ON scores(decision);
CREATE INDEX IF NOT EXISTS idx_comps_lot ON comps(lot_id);
CREATE INDEX IF NOT EXISTS idx_liens_lot ON liens(lot_id);
