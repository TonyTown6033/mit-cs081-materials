function g(l) {
    var text = '(<a class="$a" href="labs/guidance.html">"$a"</a>)'.replace(/"\$a"/g, l);
    document.write(text);
}
