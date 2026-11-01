// What the upload zone accepts. Pure bits are exported so they can be tested without a browser.

export const AUDIO_EXTENSIONS = ['.wav', '.mp3', '.m4a']

/** Only audio by extension, and no macOS sidecar files (`._call.mp3`) or other hidden files. */
export function isAudioName(name: string): boolean {
  const base = name.split('/').pop() ?? name
  if (base.startsWith('.')) return false
  const lower = base.toLowerCase()
  return AUDIO_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

export function keepAudio<T extends { name: string }>(files: T[]): T[] {
  return files.filter((f) => isAudioName(f.name))
}

// The parts of FileSystemEntry we use, so tests can hand in plain objects
export interface DroppedEntry {
  isFile: boolean
  isDirectory: boolean
  file?: (ok: (f: File) => void, fail: (e: unknown) => void) => void
  createReader?: () => { readEntries: (ok: (e: DroppedEntry[]) => void, fail: (e: unknown) => void) => void }
}

/** Every file under these entries, descending into folders. readEntries gives batches (100 at a
 *  time in Chrome) until it returns an empty one, so it has to be called until then. */
export async function filesFromEntries(entries: DroppedEntry[]): Promise<File[]> {
  const out: File[] = []
  for (const entry of entries) {
    if (entry.isFile && entry.file) {
      out.push(await new Promise<File>((ok, fail) => entry.file!(ok, fail)))
    } else if (entry.isDirectory && entry.createReader) {
      const reader = entry.createReader()
      for (;;) {
        const batch = await new Promise<DroppedEntry[]>((ok, fail) => reader.readEntries(ok, fail))
        if (!batch.length) break
        out.push(...(await filesFromEntries(batch)))
      }
    }
  }
  return out
}

/** The audio in a drop. `webkitGetAsEntry` only works during the drop event itself, so the
 *  entries are taken before the first await. */
export function audioFromDrop(data: DataTransfer): Promise<File[]> {
  const entries = Array.from(data.items ?? [])
    .map((item) => item.webkitGetAsEntry?.() as unknown as DroppedEntry | null)
    .filter((e): e is DroppedEntry => !!e)
  const plain = Array.from(data.files)
  return entries.length ? filesFromEntries(entries).then(keepAudio) : Promise.resolve(keepAudio(plain))
}
