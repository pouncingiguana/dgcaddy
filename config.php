<?php
// Edit these values for your server.
const DB_DSN    = 'mysql:host=localhost;dbname=caddy;charset=utf8mb4';
const DB_USER   = 'caddy';
const DB_PASS   = 'change-me';
const API_TOKEN = 'change-me-to-a-long-random-password';  // the pages ask for this once per device

const PHP_CLI   = '/usr/bin/php';                          // command-line PHP (not the Apache binary)
const PYTHON    = '/usr/bin/python3';                      // needs: requests numpy pillow matplotlib reportlab
const GENERATOR = __DIR__ . '/generate_caddybook.py';
const BOOKS_DIR = __DIR__ . '/books';                      // must be writable by the user that runs the worker
