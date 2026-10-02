<?php
require __DIR__ . '/config.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function out($code, $arr) { http_response_code($code); echo json_encode($arr, JSON_UNESCAPED_SLASHES); exit; }

// ---- auth (token sent in the X-Token header) ----
$tok = $_SERVER['HTTP_X_TOKEN'] ?? '';
if (!hash_equals(API_TOKEN, $tok)) out(401, ['error' => 'Wrong or missing password.']);

try {
    $db = new PDO(DB_DSN, DB_USER, DB_PASS, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (Exception $e) {
    // The browser only sees a generic message; the real reason goes to the PHP/Apache error log.
    error_log('caddy api: database connection failed: ' . $e->getMessage());
    out(500, ['error' => 'Database connection failed. The reason is in the server error log.']);
}

$a    = $_GET['a'] ?? '';
$body = json_decode(file_get_contents('php://input'), true) ?: [];
$now  = date('Y-m-d H:i:s');

function valid_id($s) { return is_string($s) && preg_match('/^[0-9a-f-]{36}$/i', $s); }

// A hole is complete when it has a tee and a pin with coordinates.
function incomplete_holes($data) {
    $bad = [];
    foreach (($data['holes'] ?? []) as $h) {
        $ok = isset($h['tee']['lat'], $h['tee']['lon'], $h['pin']['lat'], $h['pin']['lon']);
        if (!$ok) $bad[] = $h['n'] ?? '?';
    }
    return $bad;
}

function book_links($row) {
    if ($row['status'] !== 'built') return null;
    $base = 'books/' . $row['public_id'] . '/';
    return ['print' => $base . 'print.pdf', 'phone' => $base . 'phone.pdf', 'web' => $base . 'index.html'];
}

switch ($a) {

case 'list':
    $rows = $db->query("SELECT id,name,rev,status,updated_at,built_at FROM courses ORDER BY updated_at DESC")->fetchAll();
    out(200, ['courses' => $rows]);

case 'get':
    $s = $db->prepare("SELECT * FROM courses WHERE id=?");
    $s->execute([$_GET['id'] ?? '']);
    $r = $s->fetch();
    if (!$r) out(404, ['error' => 'Course not found.']);
    out(200, [
        'id' => $r['id'], 'name' => $r['name'], 'rev' => (int)$r['rev'], 'status' => $r['status'],
        'build_msg' => $r['build_msg'], 'built_at' => $r['built_at'],
        'links' => book_links($r), 'data' => json_decode($r['data'], true),
    ]);

case 'status':
    $s = $db->prepare("SELECT public_id,status,build_msg,built_at FROM courses WHERE id=?");
    $s->execute([$_GET['id'] ?? '']);
    $r = $s->fetch();
    if (!$r) out(404, ['error' => 'Course not found.']);
    out(200, ['status' => $r['status'], 'build_msg' => $r['build_msg'], 'built_at' => $r['built_at'], 'links' => book_links($r)]);

case 'save':
    // body: {id, data:{holes,units,courseName}, base_rev (the rev this edit started from; 0 for a new course), force}
    $id = $body['id'] ?? '';
    if (!valid_id($id)) out(400, ['error' => 'Bad course id.']);
    $data = $body['data'] ?? null;
    if (!is_array($data) || !isset($data['holes']) || !is_array($data['holes'])) out(400, ['error' => 'No hole data in request.']);
    $json = json_encode($data, JSON_UNESCAPED_SLASHES);
    if (strlen($json) > 2000000) out(413, ['error' => 'Course data is too large.']);
    $name = mb_substr(trim($data['courseName'] ?? ''), 0, 200);
    $base = (int)($body['base_rev'] ?? 0);

    $db->beginTransaction();
    $s = $db->prepare("SELECT rev FROM courses WHERE id=? FOR UPDATE");
    $s->execute([$id]);
    $r = $s->fetch();
    if (!$r) {
        $pub = bin2hex(random_bytes(8));
        $db->prepare("INSERT INTO courses (id,public_id,name,data,rev,status,created_at,updated_at) VALUES (?,?,?,?,1,'draft',?,?)")
           ->execute([$id, $pub, $name, $json, $now, $now]);
        $db->commit();
        out(200, ['rev' => 1, 'status' => 'draft']);
    }
    if ((int)$r['rev'] !== $base && empty($body['force'])) {
        $db->rollBack();
        out(409, ['error' => 'This course was changed somewhere else since you loaded it.', 'server_rev' => (int)$r['rev']]);
    }
    $new = (int)$r['rev'] + 1;
    // Any edit sends a finished or queued book back to draft: the book no longer matches the data.
    $db->prepare("UPDATE courses SET name=?, data=?, rev=?, status='draft', build_msg=NULL, updated_at=? WHERE id=?")
       ->execute([$name, $json, $new, $now, $id]);
    $db->commit();
    out(200, ['rev' => $new, 'status' => 'draft']);

case 'complete':
    // Marks the layout finished and queues the book build. The worker picks it up within a minute.
    $id = $body['id'] ?? '';
    if (!valid_id($id)) out(400, ['error' => 'Bad course id.']);
    $s = $db->prepare("SELECT data,status FROM courses WHERE id=?");
    $s->execute([$id]);
    $r = $s->fetch();
    if (!$r) out(404, ['error' => 'Save the course to the server first.']);
    if (in_array($r['status'], ['queued', 'building'], true)) out(200, ['status' => $r['status']]);
    $d = json_decode($r['data'], true);
    if (empty($d['holes'])) out(400, ['error' => 'The course has no holes.']);
    $bad = incomplete_holes($d);
    if ($bad) out(400, ['error' => 'Holes missing a tee or pin: ' . implode(', ', $bad) . '.']);
    $db->prepare("UPDATE courses SET status='queued', build_msg=NULL, queued_at=? WHERE id=?")->execute([$now, $id]);
    out(200, ['status' => 'queued']);

case 'delete':
    $id = $body['id'] ?? '';
    if (!valid_id($id)) out(400, ['error' => 'Bad course id.']);
    $s = $db->prepare("SELECT public_id FROM courses WHERE id=?");
    $s->execute([$id]);
    $r = $s->fetch();
    if ($r) {
        $dir = BOOKS_DIR . '/' . $r['public_id'];
        if (is_dir($dir)) { foreach (glob($dir . '/*') as $f) { if (is_file($f)) @unlink($f); } @rmdir($dir); }
        $db->prepare("DELETE FROM courses WHERE id=?")->execute([$id]);
    }
    out(200, ['deleted' => true]);

default:
    out(400, ['error' => 'Unknown action.']);
}
