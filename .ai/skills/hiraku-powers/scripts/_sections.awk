# Fence-aware Markdown scanner for the hiraku-powers scripts (POSIX awk).
#   -v mode=h2        print each top-level "## " heading of a body
#   -v mode=plan      print the "## Plan" section of a body
#   -v mode=h3        print each "### " heading (run on a plan)
#   -v mode=planhash  print a plan for hashing: ticks normalised, trailing
#                     whitespace dropped, everything from "### Deviations" on cut
# Headings inside fenced code (``` or ~~~) do not count, so a plan may quote
# Markdown.
function strip(s) { sub(/\r$/, "", s); sub(/[ \t]+$/, "", s); return s }

function fence(line,    lead, rest, c, n) {
  match(line, /^ */)
  lead = RLENGTH
  if (lead > 3) return 0
  rest = substr(line, lead + 1)
  c = substr(rest, 1, 1)
  if (c != "`" && c != "~") return 0
  n = 0
  while (substr(rest, n + 1, 1) == c) n++
  if (n < 3) return 0
  if (fchar == "") { fchar = c; flen = n; return 1 }
  if (c == fchar && n >= flen && substr(rest, n + 1) ~ /^[ \t]*$/) { fchar = ""; return 1 }
  return 0
}

BEGIN { fchar = ""; inplan = 0; cut = 0 }
{
  line = strip($0)
  plain = !fence(line) && fchar == ""
  if (mode == "h2") { if (plain && line ~ /^## /) print line; next }
  if (mode == "plan") {
    if (plain && line ~ /^## /) inplan = (line == "## Plan")
    if (inplan) print $0
    next
  }
  if (mode == "h3") { if (plain && line ~ /^### /) print line; next }
  if (mode == "planhash") {
    if (plain && line == "### Deviations") cut = 1
    if (cut) next
    if (plain && line ~ /^[ \t]*([-*+]|[0-9]+[.)]) \[[xX]\]/) sub(/\[[xX]\]/, "[ ]", line)
    print line
  }
}
