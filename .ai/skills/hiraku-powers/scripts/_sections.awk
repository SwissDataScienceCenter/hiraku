# Fence-aware Markdown section scanner shared by pull and push.
#   -v mode=split -v want=head|Summary|Plan|rest   print that part of the body
#   -v mode=headings                               print each top-level "## " heading
# A "## " line only counts outside fenced code (``` or ~~~, any length >= 3, a
# closing fence at least as long as the opener). Trailing whitespace and CR are
# ignored when matching headings. Text before the first heading is "head".
function strip(s) { sub(/\r$/, "", s); sub(/[ \t]+$/, "", s); return s }
BEGIN { fchar = ""; flen = 0; sec = "head" }
{
  line = strip($0)
  isfence = 0
  if (match(line, /^ {0,3}(`{3,}|~{3,})/)) {
    marker = substr(line, RSTART, RLENGTH); sub(/^ +/, "", marker)
    c = substr(marker, 1, 1); n = length(marker)
    tail = substr(line, RSTART + RLENGTH)
    if (fchar == "") {
      if (!(c == "`" && index(tail, "`"))) { fchar = c; flen = n; isfence = 1 }
    } else if (c == fchar && n >= flen && tail ~ /^[ \t]*$/) {
      fchar = ""; isfence = 1
    }
  }
  heading = (fchar == "" && !isfence && line ~ /^## /)
  if (heading) {
    if (mode == "headings") print line
    sec = (line == "## Summary") ? "Summary" : (line == "## Plan") ? "Plan" : "rest"
  }
  if (mode == "split" && sec == want) print $0
}
