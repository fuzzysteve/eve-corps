<?php
$base_dir='/home/web/corps';
require_once $base_dir.'/htdocs/vendor/autoload.php';
$loader = new \Twig\Loader\FilesystemLoader($base_dir.'/templates');

$twig = new \Twig\Environment($loader, [
    'cache' => $base_dir.'/cache',
]);
?>
