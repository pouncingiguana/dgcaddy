-- Run once:  mysql -u root -p caddy < schema.sql
CREATE TABLE IF NOT EXISTS courses (
  id         CHAR(36)     NOT NULL PRIMARY KEY,   -- made on the phone, so offline capture can upload later
  public_id  CHAR(16)     NOT NULL UNIQUE,        -- random; names the output folder in books/
  name       VARCHAR(200) NOT NULL DEFAULT '',
  data       LONGTEXT     NOT NULL,               -- the course JSON: holes, tees, pins, waypoints, par, OB
  rev        INT          NOT NULL DEFAULT 1,     -- +1 on every save; detects edits made elsewhere
  status     ENUM('draft','queued','building','built','failed') NOT NULL DEFAULT 'draft',
  build_msg  TEXT         NULL,                   -- error output when a build fails
  created_at DATETIME     NOT NULL,
  updated_at DATETIME     NOT NULL,
  queued_at  DATETIME     NULL,
  built_at   DATETIME     NULL,
  KEY idx_status (status, queued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
