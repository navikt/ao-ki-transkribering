/* Only the two latest successful versions are retained, in page memory. */
const summaryDiffer = typeof module !== "undefined"
  ? require("./vendor/jsdiff/dist/diff.min.js") : Diff;

function summaryDiffHtml(previous, current, diff = summaryDiffer.diffWordsWithSpace) {
  if (previous === current) return "Ingen endringer.";
  const escape = value => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  let changes;
  // Bound synchronous diff work so a large rewrite cannot freeze recording controls.
  if (previous.length + current.length <= 100000) {
    try { changes = diff(previous, current, {timeout: 40, maxEditLength: 2000}); }
    catch { /* A whole-text replacement remains a valid comparison. */ }
  }
  changes ||= [{value: previous, removed: true}, {value: current, added: true}];
  return changes.map(part => {
    const text = escape(part.value);
    return part.added ? `<ins>${text}</ins>` : part.removed ? `<del>${text}</del>` : text;
  }).join("");
}

class SummaryComparison {
  constructor(details, body) {
    Object.assign(this, {details, body});
    details.addEventListener("toggle", () => this.render());
    this.reset();
  }
  update(text) {
    this.previous = this.current;
    this.current = text;
    this.details.hidden = this.previous === null;
    this.body.innerHTML = "";
    this.render();
  }
  render() {
    if (this.details.open && this.previous !== null) {
      this.body.innerHTML = summaryDiffHtml(this.previous, this.current);
    }
  }
  reset() {
    this.previous = this.current = null;
    this.details.hidden = true;
    this.details.open = false;
    this.body.innerHTML = "";
  }
}

if (typeof module !== "undefined") module.exports = {summaryDiffHtml, SummaryComparison};
