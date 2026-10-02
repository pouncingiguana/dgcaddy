<?php
// Builds queued caddy books. Run from cron every minute:
//   * * * * * /usr/bin/php /path/to/worker.php >> /path/to/worker.log 2>&1
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }
require __DIR__ . '/config.php';
require __DIR__ . '/notify.php';

// Run with -v to see a message when there is nothing to do (cron runs stay quiet).
$verbose = in_array('-v', $argv ?? [], true);

// The lock lives in the books folder, the one place this user already needs write access to.
$lock = @fopen(BOOKS_DIR . '/.worker.lock', 'c');
if (!$lock) {
    fwrite(STDERR, "worker: cannot create " . BOOKS_DIR . "/.worker.lock. The books folder must exist and be writable by the user running the worker.\n");
    exit(1);
}
if (!flock($lock, LOCK_EX | LOCK_NB)) {   // a build is already running
    if ($verbose) echo "worker: another build is already running.\n";
    exit;
}

$db = new PDO(DB_DSN, DB_USER, DB_PASS, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);

// A previous run that died mid-build leaves 'building' behind. We hold the lock, so nothing is really building.
$db->exec("UPDATE courses SET status='queued' WHERE status='building'");

$built = 0;
while (true) {
    $row = $db->query("SELECT id,public_id,name,data,rev FROM courses WHERE status='queued' ORDER BY queued_at LIMIT 1")->fetch();
    if (!$row) break;
    $built++;

    $db->prepare("UPDATE courses SET status='building' WHERE id=?")->execute([$row['id']]);
    $title = $row['name'] !== '' ? $row['name'] : 'Untitled course';
    $holes = count((json_decode($row['data'], true) ?: [])['holes'] ?? []);
    $t0 = microtime(true);
    discord_notify('Building: ' . $title, 'The builder picked this course up. Downloading satellite imagery and drawing pages.', 0x38bdf8,
        [['name' => 'Holes', 'value' => (string)$holes, 'inline' => true]]);
    $dir = BOOKS_DIR . '/' . $row['public_id'];
    $tmp = BOOKS_DIR . '/.tmp-' . $row['public_id'];
    @mkdir($tmp, 0775, true);
    file_put_contents($tmp . '/course.json', $row['data']);

    // Contract with the generator: generate_caddybook.py <course.json> <output dir>
    // It writes print.pdf, phone.pdf and index.html into the output dir and exits 0 on success.
    $cmd = escapeshellarg(PYTHON) . ' ' . escapeshellarg(GENERATOR) . ' '
         . escapeshellarg($tmp . '/course.json') . ' ' . escapeshellarg($tmp) . ' 2>&1';
    $output = []; $code = 0;
    exec($cmd, $output, $code);
    $log = implode("\n", array_slice($output, -40));

    $missing = [];
    foreach (['print.pdf', 'phone.pdf', 'index.html'] as $f) if (!is_file($tmp . '/' . $f)) $missing[] = $f;

    // Edits during the build set the status back to draft; only mark built if nothing changed.
    $cur = $db->prepare("SELECT rev FROM courses WHERE id=?");
    $cur->execute([$row['id']]);
    $now = $cur->fetch();

    if ($code === 0 && !$missing) {
        @mkdir($dir, 0775, true);
        foreach (glob($tmp . '/*') as $f) { if (basename($f) !== 'course.json') rename($f, $dir . '/' . basename($f)); }
        $unchanged = $now && (int)$now['rev'] === (int)$row['rev'];
        $db->prepare("UPDATE courses SET status=?, build_msg=NULL, built_at=? WHERE id=? AND status='building'")
           ->execute([$unchanged ? 'built' : 'draft', date('Y-m-d H:i:s'), $row['id']]);

        $files = ['print.pdf' => 'Print PDF', 'phone.pdf' => 'Phone PDF', 'index.html' => 'Web page'];
        $lines = [];
        foreach ($files as $f => $label) {
            $url = book_url($row['public_id'], $f);
            $lines[] = $url !== '' ? "[$label]($url)" : "$label: books/{$row['public_id']}/$f";
        }
        $desc = implode("\n", $lines);
        if (book_url($row['public_id'], '') === '') $desc .= "\n\nSet SITE_URL in config.php to get clickable links.";
        if (!$unchanged) $desc .= "\n\nThe course was edited while this was building. Mark it complete again to rebuild with the latest changes.";
        discord_notify('Book ready: ' . $title, $desc, 0x34d399, [
            ['name' => 'Holes', 'value' => (string)$holes, 'inline' => true],
            ['name' => 'Build time', 'value' => round(microtime(true) - $t0) . ' s', 'inline' => true],
        ]);
    } else {
        $msg = $code !== 0 ? "Generator exited with code $code.\n$log" : 'Generator finished but did not create: ' . implode(', ', $missing) . "\n$log";
        $db->prepare("UPDATE courses SET status='failed', build_msg=? WHERE id=? AND status='building'")->execute([$msg, $row['id']]);
        discord_notify('Build failed: ' . $title, "```\n" . notify_clip(str_replace('```', "'''", $msg), 1500) . "\n```", 0xef476f, [
            ['name' => 'Holes', 'value' => (string)$holes, 'inline' => true],
        ]);
    }
    foreach (glob($tmp . '/*') as $f) @unlink($f);
    @rmdir($tmp);
    echo date('c'), ' ', $row['public_id'], ' exit=', $code, "\n";
}
if ($built === 0 && $verbose) echo "worker: nothing queued. Mark a course complete in the editor first.\n";
