<?php
// Builds queued caddy books. Run from cron every minute:
//   * * * * * /usr/bin/php /path/to/worker.php >> /path/to/worker.log 2>&1
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }
require __DIR__ . '/config.php';

$lock = fopen(__DIR__ . '/.worker.lock', 'c');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) exit;   // a build is already running

$db = new PDO(DB_DSN, DB_USER, DB_PASS, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);

// A previous run that died mid-build leaves 'building' behind. We hold the lock, so nothing is really building.
$db->exec("UPDATE courses SET status='queued' WHERE status='building'");

while (true) {
    $row = $db->query("SELECT id,public_id,data,rev FROM courses WHERE status='queued' ORDER BY queued_at LIMIT 1")->fetch();
    if (!$row) break;

    $db->prepare("UPDATE courses SET status='building' WHERE id=?")->execute([$row['id']]);
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
    } else {
        $msg = $code !== 0 ? "Generator exited with code $code.\n$log" : 'Generator finished but did not create: ' . implode(', ', $missing) . "\n$log";
        $db->prepare("UPDATE courses SET status='failed', build_msg=? WHERE id=? AND status='building'")->execute([$msg, $row['id']]);
    }
    foreach (glob($tmp . '/*') as $f) @unlink($f);
    @rmdir($tmp);
    echo date('c'), ' ', $row['public_id'], ' exit=', $code, "\n";
}
