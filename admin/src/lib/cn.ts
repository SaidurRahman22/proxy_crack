export type ClassValue = string | false | null | undefined

/** Join class names, dropping falsy entries. */
export function cn(...values: ClassValue[]): string {
  let out = ''
  for (const value of values) {
    if (!value) continue
    out = out === '' ? value : `${out} ${value}`
  }
  return out
}
