// Typesets $...$ / $$...$$ (and \(...\) / \[...\]) math wherever it shows
// up in the page -- mentor replies, Math Lab steps, graded practice
// feedback, lesson content -- without any of those call sites needing to
// know KaTeX exists. Deferred and loaded right after katex.min.js and
// auto-render.min.js (also deferred, same script order), same reasoning
// as htmx-config.js: a deferred *external* script, not an inline one, is
// what makes "the library already exists" reliable on the very first
// render, not just after htmx's first swap.
//
// KaTeX auto-render already skips <script>/<style>/<textarea>/<pre>/<code>
// by default, so fenced code blocks (Pygments spans) are never mistaken
// for math even though they can contain a literal "$".
(function () {
  var DELIMITERS = [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\(", right: "\\)", display: false },
    { left: "\\[", right: "\\]", display: true },
  ];

  function renderMath(root) {
    if (!root || typeof renderMathInElement !== "function") return;
    renderMathInElement(root, { delimiters: DELIMITERS, throwOnError: false });
  }

  renderMath(document.body);
  // Every mentor turn, quick action, Math Lab result and graded review
  // answer arrives via an htmx swap -- re-run over just the swapped-in
  // subtree rather than the whole page.
  document.body.addEventListener("htmx:afterSwap", function (e) {
    renderMath(e.detail.target);
  });
})();
