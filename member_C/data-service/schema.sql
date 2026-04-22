CREATE TABLE IF NOT EXISTS submissions (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    poster_filename  TEXT NOT NULL DEFAULT '',
    poster_image     BLOB,
    poster_mime_type TEXT,
    poster_size      INTEGER,
    status           TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING', 'READY', 'NEEDS REVISION', 'INCOMPLETE')),
    note             TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
