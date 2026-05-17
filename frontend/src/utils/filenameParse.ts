// filename 格式：113-2813-C-008-084-M_王大明_研究題目名稱.pdf
// parts[0] = project code（含年份和類別字母）
// parts[1] = 學生姓名
// parts[2..] = 研究題目（join '_'）

export function getYear(filename: string): string {
  return filename.match(/^(\d{2,3})-/)?.[1] ?? ''
}

export function getCategory(filename: string): string {
  const letter = filename.match(/-([BEHMS])_/)?.[1]
  const map: Record<string, string> = {
    B: '生物科學類',
    E: '工程技術類',
    H: '人文及社會科學類',
    M: '自然科學類',
    S: '科學教育類',
  }
  return letter ? (map[letter] ?? '') : ''
}

export function getStudentName(filename: string): string {
  const parts = filename.replace(/\.pdf$/i, '').split('_')
  return parts.length >= 2 ? parts[1] : ''
}

export function getTitle(filename: string): string {
  const parts = filename.replace(/\.pdf$/i, '').split('_')
  if (parts.length >= 3) return parts.slice(2).join('_')
  // fallback：若格式不符，回傳第 2 段後的所有內容
  return parts.slice(1).join('_')
}
