-- PostGIS schema for the AUDA RDP plot layer.
--
-- STATUS: this is the TARGET shape. Nothing in this repo populates it.
-- The PDF is a raster zoning drawing; it contains no ownership, survey history
-- or legal status, so `plots` cannot be filled from it. The OCR pipeline fills
-- `ocr_numbers` (best-effort text + zone read from the plan's fill colour).
-- `plots` is for the vector/attribute source from AUDA, when it arrives.
--
--   psql "$DATABASE_URL" -f db/schema.sql
--
-- Geometry is EPSG:3857 to match the tile pyramid and data/bounds.json. Store a
-- 4329 copy alongside if you need lon/lat for search or WFS output.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram search on plot numbers

-- ---------------------------------------------------------------- sheets
-- A published plan drawing this data derives from.
CREATE TABLE IF NOT EXISTS sheets (
    id           text PRIMARY KEY,              -- e.g. 'auda-rdp-2022'
    title        text NOT NULL,
    source_uri   text,                         -- where the original came from
    published_on date,
    -- the extent, so the app never hardcodes bounds again
    extent       geometry(Polygon, 3857) NOT NULL,
    m_per_px     numeric(10,4),
    native_zoom  numeric(4,2),
    -- provenance and limits travel WITH the data
    accuracy_note text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sheets_extent_gix ON sheets USING gist (extent);

-- ----------------------------------------------------------------- zones
-- Land-use classes. The plan's own colours, not a taxonomy we invented.
CREATE TABLE IF NOT EXISTS land_use_zones (
    id          smallserial PRIMARY KEY,
    code        text UNIQUE NOT NULL,          -- 'RES', 'OPEN', 'INST', 'COM', 'IND'
    name        text NOT NULL,
    colour      char(7),                        -- '#f5e93a' as drawn on the plan
    description text
);

-- ---------------------------------------------------------------- plots
-- One row per plot/parcel. NOT populateable from the PDF - this is for the
-- AUDA vector + attribute source.
CREATE TABLE IF NOT EXISTS plots (
    id            bigserial PRIMARY KEY,
    sheet_id      text NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    zone_id       smallint REFERENCES land_use_zones(id),

    -- plot identifiers. `plot_no` is what the user reads on the drawing;
    -- `survey_no` is the revenue-record key, which only the attribute source
    -- can supply. Do not invent one from OCR.
    plot_no    text,
    survey_no  text,
    fp_no      text,                           -- Final Plot, post-TP-scheme
    rp_no      text,                           -- Reconstituted Plot

    area_sqm   numeric(14,2),
    land_use   text,
    far        numeric(6,2),                    -- floor area ratio
    height_m   numeric(6,2),                    -- permissible height

    -- legal status is the whole reason this product exists; it is NOT derivable
    -- from a map. Null means unknown, never "clear".
    status     text,
    approved_on date,
    source_ref text,                           -- provenance for the attribute

    geom       geometry(MultiPolygon, 3857) NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT plots_area_positive CHECK (area_sqm IS NULL OR area_sqm >= 0)
);

-- spatial index is what makes click-to-identify and viewport queries viable
CREATE INDEX IF NOT EXISTS plots_geom_gix  ON plots USING gist (geom);
CREATE INDEX IF NOT EXISTS plots_sheet_gix ON plots USING gist (sheet_id, geom);
CREATE INDEX IF NOT EXISTS plots_no_trgm   ON plots USING gin (plot_no gin_trgm_ops);
CREATE INDEX IF NOT EXISTS plots_survey_trgm ON plots USING gin (survey_no gin_trgm_ops);
CREATE INDEX IF NOT EXISTS plots_lookup_idx ON plots (sheet_id, plot_no);

-- Bounding-box index: most map interactions are rectangle queries long before
-- they are point-in-polygon, and this keeps the viewport cheap.
CREATE INDEX IF NOT EXISTS plots_geom_idx ON plots USING gist (geom);

-- --------------------------------------------------------- ocr_numbers
-- What the OCR actually produced. Kept separate from `plots` on purpose: these
-- are unverified text reads and must never be conflated with real identifiers.
CREATE TABLE IF NOT EXISTS ocr_numbers (
    id         bigserial PRIMARY KEY,
    sheet_id   text NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,

    raw_text   text NOT NULL,                  -- exactly as read
    norm_text  text NOT NULL,                  -- whitespace-collapsed
    confidence numeric(4,3) NOT NULL,          -- 0..1, from the recogniser

    -- land-use zone read from the plan's own fill colour under the number
    zone_id    smallint REFERENCES land_use_zones(id),
    zone_colour char(7),

    -- where it sits in the source scan, for cropping and re-OCR at other scales
    src_px     integer,
    src_py     integer,
    rotation_deg numeric(5,1),

    geom       geometry(Point, 3857) NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    -- a recogniser that returns 0.0..1.0 and nothing looser
    CONSTRAINT ocr_conf_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS ocr_numbers_geom_gix ON ocr_numbers USING gist (geom);
CREATE INDEX IF NOT EXISTS ocr_numbers_text_trgm ON ocr_numbers USING gin (norm_text gin_trgm_ops);

-- Confidence is the ONLY thing standing between an OCR guess and a parcel
-- identifier a user might act on, so it must be queryable.
CREATE INDEX IF NOT EXISTS ocr_numbers_conf_idx ON ocr_numbers (confidence DESC);

-- --------------------------------------------------------------- helper
-- Click-to-identify: what is under this point?
--
-- Deliberately returns confidence on every hit. A caller that wants to show
-- this to a user should be able to refuse anything below a threshold rather
-- than discover it cannot trust the row.
CREATE OR REPLACE FUNCTION identify_at(
    px double precision,
    py double precision,
    sheet text,
    min_conf numeric DEFAULT 0.6
)
RETURNS TABLE (
    plot_id     bigint,
    plot_no     text,
    survey_no   text,
    zone_name   text,
    area_sqm    numeric,
    ocr_text    text,
    ocr_conf    numeric
)
LANGUAGE sql STABLE AS $$
    SELECT
        p.id,
        p.plot_no,
        p.survey_no,
        z.name,
        p.area_sqm,
        o.norm_text,
        o.confidence
    FROM plots p
    LEFT JOIN land_use_zones z ON z.id = p.zone_id
    LEFT JOIN LATERAL (
        SELECT n.norm_text, n.confidence
        FROM ocr_numbers n
        WHERE n.sheet_id = sheet
          AND n.confidence >= min_conf
          AND ST_DWithin(n.geom, ST_SetSRID(ST_MakePoint(px, py), 3857), 60)
        ORDER BY n.geom <-> ST_SetSRID(ST_MakePoint(px, py), 3857)
        LIMIT 1
    ) o ON true
    WHERE p.sheet_id = sheet
      AND ST_Intersects(p.geom, ST_SetSRID(ST_MakePoint(px, py), 3857))
    LIMIT 1;
$$;

-- Viewport query for the map, with confidence filtering applied in SQL so the
-- client never has to receive rows it will only discard.
CREATE OR REPLACE VIEW plot_numbers AS
SELECT
    o.id,
    o.norm_text  AS plot_no,
    o.confidence,
    o.zone_colour,
    z.name       AS zone_name,
    o.geom
FROM ocr_numbers o
LEFT JOIN land_use_zones z ON z.id = o.zone_id
WHERE o.confidence >= 0.6;

COMMIT;

-- Seed the zones read from the plan's legend. Colour values are the fills as
-- drawn; hue-band classification in tools/make_geojson.py maps them.
-- INSERT INTO land_use_zones (code, name, colour) VALUES
--   ('RES',  'Residential',           '#f5e93a'),
--   ('OPEN', 'Open / agricultural',   '#2fa84f'),
--   ('INST', 'Institutional / public','#a86fd6'),
--   ('COM',  'Commercial / mixed',    '#ef8a1f'),
--   ('IND',  'Industrial',            '#d9342b'),
--   ('WATER','Water / open',          '#7fc8e8');
