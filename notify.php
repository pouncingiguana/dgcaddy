<?php
// Discord webhook notifications for the caddy book queue.
// Turned on by DISCORD_WEBHOOK in config.php; empty or undefined means off.
// Nothing here ever throws, and a slow or unreachable Discord can't hold up a build for more than a few seconds.

/** First $n characters of a string, without splitting a multibyte character and without the mbstring module. */
function notify_clip($s, $n) {
    return preg_match('/^.{0,' . (int)$n . '}/su', (string)$s, $m) ? $m[0] : '';
}

/** Absolute link to a file of a built book, or '' when SITE_URL isn't set. */
function book_url($public_id, $file) {
    if (!defined('SITE_URL') || SITE_URL === '') return '';
    return rtrim(SITE_URL, '/') . '/books/' . $public_id . '/' . $file;
}

/**
 * Post one embed to the webhook.
 * $fields: list of ['name' => ..., 'value' => ..., 'inline' => bool]
 */
function discord_notify($title, $description = '', $color = 0x38bdf8, $fields = []) {
    if (!defined('DISCORD_WEBHOOK') || DISCORD_WEBHOOK === '') return false;

    $cleanFields = [];
    foreach (array_slice($fields, 0, 10) as $f) {
        $cleanFields[] = [
            'name'   => notify_clip($f['name'], 250) ?: '-',
            'value'  => notify_clip($f['value'], 1000) ?: '-',
            'inline' => !empty($f['inline']),
        ];
    }
    $payload = json_encode([
        'username'         => 'Caddy Book',
        'allowed_mentions' => ['parse' => []],   // a course name like "@everyone" must not ping anyone
        'embeds'           => [[
            'title'       => notify_clip($title, 250),
            'description' => notify_clip($description, 3500),
            'color'       => $color,
            'fields'      => $cleanFields,
            'timestamp'   => gmdate('c'),
        ]],
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    if ($payload === false) return false;

    $ok = false; $why = '';
    try {
        if (function_exists('curl_init')) {
            $ch = curl_init(DISCORD_WEBHOOK);
            curl_setopt_array($ch, [
                CURLOPT_POST => true,
                CURLOPT_POSTFIELDS => $payload,
                CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_CONNECTTIMEOUT => 3,
                CURLOPT_TIMEOUT => 5,
            ]);
            $res  = curl_exec($ch);
            $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            $ok   = $res !== false && $code >= 200 && $code < 300;
            $why  = $res === false ? curl_error($ch) : "HTTP $code";
        } elseif (ini_get('allow_url_fopen')) {
            $ctx = stream_context_create(['http' => [
                'method' => 'POST', 'header' => "Content-Type: application/json\r\n",
                'content' => $payload, 'timeout' => 5, 'ignore_errors' => true,
            ]]);
            $res  = @file_get_contents(DISCORD_WEBHOOK, false, $ctx);
            $line = $http_response_header[0] ?? '';
            $ok   = $res !== false && preg_match('#^HTTP/\S+\s+2\d\d#', $line);
            $why  = $line ?: 'no response';
        } else {
            $why = 'neither the curl extension nor allow_url_fopen is available';
        }
    } catch (Throwable $e) {
        $why = $e->getMessage();
    }
    // The webhook URL is a secret, so it is never written to the log.
    if (!$ok) error_log('caddy notify: Discord message not sent (' . $why . ')');
    return (bool)$ok;
}
