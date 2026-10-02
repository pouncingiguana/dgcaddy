<?php
// TEMPLATE. Copy this file to config.php on the server and fill in the real values.
// config.php is listed in .gitignore, so it is never committed. Keep real secrets out of this file.
const DB_DSN    = 'mysql:host=localhost;dbname=caddy;charset=utf8mb4';
const DB_USER   = 'caddy';
const DB_PASS   = 'change-me';
const API_TOKEN = 'change-me-to-a-long-random-password';  // the pages ask for this once per device

const PHP_CLI   = '/usr/bin/php';                          // command-line PHP (not the Apache binary)
const PYTHON    = '/usr/bin/python3';                      // needs: requests pillow matplotlib reportlab
const GENERATOR = __DIR__ . '/generate_caddybook.py';
const BOOKS_DIR = __DIR__ . '/books';                      // must be writable by the user that runs the worker
