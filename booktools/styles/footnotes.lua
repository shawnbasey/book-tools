-- Turn pandoc notes (including citeproc's Chicago notes) into inline spans so WeasyPrint's
-- `float: footnote` places them at the bottom of the page instead of at the end of the book.
function Note(el)
  local inlines = pandoc.utils.blocks_to_inlines(el.content, { pandoc.Space() })
  return pandoc.Span(inlines, { class = "footnote" })
end
