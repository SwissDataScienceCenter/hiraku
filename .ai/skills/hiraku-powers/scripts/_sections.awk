# Fence-aware Markdown scanner shared by the hiraku-powers scripts. Written for
# POSIX awk (mawk, gawk, busybox, BSD): no regex intervals, no gawk extensions.
#   -v mode=split -v want=head|Summary|Plan|rest   print that part of the body
#   -v mode=headings                               print each top-level "## " heading
#   -v mode=planbody                               print a plan for hashing: ticks
#       normalised, trailing whitespace dropped, and everything from the first
#       "### Deviations" line (outside code fences) to the end removed
#   -v mode=h3                                     print each "### " heading
#   -v mode=devtail                                print the text after the first
#       "### Deviations" line
# A heading only counts outside fenced code (``` or ~~~, at least 3, indented at
# most 3 spaces; a closing fence is at least as long as the opener). Trailing
# whitespace and CR are ignored when matching. Text before the first "## " is "head".
function strip(s) { sub(/\r$/, "", s); sub(/[ \t]+$/, "", s); return s }

# Returns 1 if the line is a fence delimiter and updates the fence state.
function fence(line,    lead, rest, c, n, tail) {
  match(line, /^ */)
  lead = RLENGTH
  if (lead > 3) return 0
  rest = substr(line, lead + 1)
  c = substr(rest, 1, 1)
  if (c != "`" && c != "~") return 0
  n = 0
  while (substr(rest, n + 1, 1) == c) n++
  if (n < 3) return 0
  tail = substr(rest, n + 1)
  if (fchar == "") {
    if (c == "`" && index(tail, "`")) return 0
    fchar = c; flen = n
    return 1
  }
  if (c == fchar && n >= flen && tail ~ /^[ \t]*$/) { fchar = ""; return 1 }
  return 0
}

BEGIN { fchar = ""; flen = 0; sec = "head"; dev = 0 }
{
  line = strip($0)
  isfence = fence(line)
  plain = (fchar == "" && !isfence)
  if (mode == "planbody") {
    if (plain && line ~ /^[ \t]*([-*+]|[0-9]+[.)]) \[[xX]\]/) sub(/\[[xX]\]/, "[ ]", line)
    if (plain && line == "### Deviations" && !dev) dev = NR
    lines[NR] = line
    next
  }
  if (mode == "h3" || mode == "devtail") {
    if (plain && line ~ /^### /) {
      if (mode == "h3") print line
      if (line == "### Deviations" && !dev) dev = NR
    }
    lines[NR] = $0
    next
  }
  if (plain && line ~ /^## /) {
    if (mode == "headings") print line
    sec = (line == "## Summary") ? "Summary" : (line == "## Plan") ? "Plan" : "rest"
  }
  if (mode == "split" && sec == want) print $0
}
END {
  if (mode == "planbody") {
    last = dev ? dev - 1 : NR
    for (i = 1; i <= last; i++) print lines[i]
  }
  if (mode == "devtail" && dev) {
    for (i = dev + 1; i <= NR; i++) print lines[i]
  }
}
