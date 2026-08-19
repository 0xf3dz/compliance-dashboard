-- Ironbark Ridge ESG schema: source data only.
--
-- Every table here is derived from a file in data/, so the pipeline drops and
-- recreates all of them on each run. That keeps a re-ingest idempotent.
--
-- incident_ai_findings is deliberately absent. AI findings cost money to
-- produce and are not derivable from the CSVs, so they live in schema_ai.sql,
-- which only creates and never drops. A re-ingest must not destroy them.

DROP TABLE IF EXISTS data_quality_issues CASCADE;
DROP TABLE IF EXISTS fuel_deliveries CASCADE;
DROP TABLE IF EXISTS electricity_readings CASCADE;
DROP TABLE IF EXISTS incidents CASCADE;
DROP TABLE IF EXISTS suppliers CASCADE;
DROP TABLE IF EXISTS emission_factors CASCADE;

CREATE TABLE emission_factors (
    activity          text NOT NULL,
    scope             int  NOT NULL CHECK (scope IN (1, 2, 3)),
    unit              text NOT NULL,
    kg_co2e_per_unit  numeric NOT NULL,
    source            text
);

-- Load-bearing. api/src/factors.ts throws on an ambiguous activity, and this
-- index stops a duplicate row from reaching it in the first place.
CREATE UNIQUE INDEX ux_emission_factors_activity ON emission_factors (activity);

CREATE TABLE fuel_deliveries (
    id            serial PRIMARY KEY,
    invoice_no    text NOT NULL,
    delivery_date date,
    date_imputed  bool NOT NULL DEFAULT false,
    fuel_type     text NOT NULL,
    quantity_l    numeric NOT NULL,
    cost_aud      numeric,
    site_area     text,
    is_credit     bool NOT NULL DEFAULT false,
    source_row    int
);

-- No sign check on quantity_l. Invoice INV-41777 is a legitimate credit of
-- -12,500 L, and rejecting it would overstate Scope 1.
CREATE UNIQUE INDEX ux_fuel_deliveries_source_row ON fuel_deliveries (source_row);
CREATE INDEX ix_fuel_deliveries_delivery_date ON fuel_deliveries (delivery_date);
CREATE INDEX ix_fuel_deliveries_fuel_type ON fuel_deliveries (fuel_type);

CREATE TABLE electricity_readings (
    id                serial PRIMARY KEY,
    meter_id          text NOT NULL,
    meter_description text,
    period            date NOT NULL,
    consumption_kwh   numeric NOT NULL CHECK (consumption_kwh >= 0),
    unit              text,
    is_flagged        bool NOT NULL DEFAULT false,
    source_row        int
);

-- One meter reports once per month. A second row for the same pair is a
-- duplicate export, not a second reading.
CREATE UNIQUE INDEX ux_electricity_readings_meter_period ON electricity_readings (meter_id, period);
CREATE UNIQUE INDEX ux_electricity_readings_source_row ON electricity_readings (source_row);
CREATE INDEX ix_electricity_readings_period ON electricity_readings (period);

CREATE TABLE incidents (
    id            serial PRIMARY KEY,
    incident_id   text NOT NULL,
    incident_date date,
    location      text,
    type_code     text,
    severity_raw  text,
    severity_norm text,
    severity_rank int CHECK (severity_rank BETWEEN 1 AND 3),
    description   text,
    source_row    int
);

-- source_row is the CSV data-row number and is the join key for AI findings,
-- because the surrogate id changes on every re-ingest.
CREATE UNIQUE INDEX ux_incidents_source_row ON incidents (source_row);
CREATE INDEX ix_incidents_incident_date ON incidents (incident_date);
CREATE INDEX ix_incidents_type_code ON incidents (type_code);

CREATE TABLE suppliers (
    id                    serial PRIMARY KEY,
    supplier_name         text NOT NULL,
    abn                   text,
    abn_well_formed       bool NOT NULL DEFAULT false,
    abn_checksum_ok       bool NOT NULL DEFAULT false,
    category              text,
    fy_spend_aud          numeric,
    canonical_supplier_id int REFERENCES suppliers(id),
    source_row            int
);

-- abn_well_formed records structure only: 11 digits after stripping spaces.
-- abn_checksum_ok records the real ATO weighted-modulus result. The two are
-- separate because every 11-digit ABN in suppliers.csv fails the checksum, so
-- a single strict flag would mark all 12 and hide the one real fault,
-- TerraForm's 7-digit value.
CREATE UNIQUE INDEX ux_suppliers_source_row ON suppliers (source_row);
CREATE INDEX ix_suppliers_canonical_supplier_id ON suppliers (canonical_supplier_id);

CREATE TABLE data_quality_issues (
    id          serial PRIMARY KEY,
    source_file text NOT NULL,
    source_row  int,
    record_ref  text,
    issue_type  text NOT NULL,
    field       text,
    raw_value   text,
    action      text NOT NULL CHECK (action IN ('fixed', 'flagged', 'rejected')),
    resolution  text,
    detail      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- The API groups the report by file and by issue type, and the frontend
-- filters on issue type alone.
CREATE INDEX ix_data_quality_issues_file_type ON data_quality_issues (source_file, issue_type);
CREATE INDEX ix_data_quality_issues_issue_type ON data_quality_issues (issue_type);
