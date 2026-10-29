import { useEffect, useId, useMemo, useRef, useState } from 'react'

export default function MultiCombobox({ label, options, selected, onToggle }) {
  const reactId = useId()
  const inputId = `${reactId}-input`
  const listId = `${reactId}-list`
  const labelId = `${reactId}-label`
  const rootRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return options
    return options.filter((option) => option.label.toLowerCase().includes(needle))
  }, [options, query])

  const highlighted = Math.min(activeIndex, Math.max(visible.length - 1, 0))
  const activeId = open && visible[highlighted] ? `${reactId}-opt-${highlighted}` : undefined

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  useEffect(() => {
    if (!open || !activeId) return
    document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' })
  }, [open, activeId])

  function onKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        return
      }
      setActiveIndex((index) => Math.min(index + 1, Math.max(visible.length - 1, 0)))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        return
      }
      setActiveIndex((index) => Math.max(index - 1, 0))
    } else if (event.key === 'Home') {
      if (!open) return
      event.preventDefault()
      setActiveIndex(0)
    } else if (event.key === 'End') {
      if (!open) return
      event.preventDefault()
      setActiveIndex(Math.max(visible.length - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        return
      }
      const option = visible[highlighted]
      if (option) onToggle(option.value)
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div className="combobox" ref={rootRef}>
      <label id={labelId} htmlFor={inputId}>
        {label}
      </label>
      <input
        id={inputId}
        role="combobox"
        aria-labelledby={labelId}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeId}
        value={query}
        placeholder={selected.length > 0 ? `${selected.length} selected` : 'All'}
        onChange={(event) => {
          setQuery(event.target.value)
          setActiveIndex(0)
          setOpen(true)
        }}
        onPointerDown={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={(event) => {
          if (!rootRef.current?.contains(event.relatedTarget)) setOpen(false)
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul id={listId} role="listbox" aria-labelledby={labelId} aria-multiselectable="true">
          {visible.length === 0 ? (
            <li className="combobox-empty" role="presentation">
              No matches
            </li>
          ) : (
            visible.map((option, index) => {
              const isSelected = selected.includes(option.value)
              return (
                <li
                  key={option.value}
                  id={`${reactId}-opt-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  className={index === highlighted ? 'active' : undefined}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseDown={(event) => {
                    event.preventDefault()
                    onToggle(option.value)
                  }}
                >
                  {option.label}
                </li>
              )
            })
          )}
        </ul>
      )}
    </div>
  )
}
