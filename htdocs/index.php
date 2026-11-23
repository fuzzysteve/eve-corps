<?php
require_once "/home/web/corps/setup.php";

$template = $twig->load('index.tmpl');
$template->display();
?>
