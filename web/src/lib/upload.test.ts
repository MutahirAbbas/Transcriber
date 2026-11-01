import { describe, expect, it } from 'vitest'

import { filesFromEntries, isAudioName, keepAudio, type DroppedEntry } from './upload'

const fileEntry = (name: string): DroppedEntry => ({
  isFile: true,
  isDirectory: false,
  file: (ok) => ok(new File(['x'], name)),
})

/** A folder that hands back its children in batches, as browsers do. */
function folder(children: DroppedEntry[], batchSize = 2): DroppedEntry {
  return {
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let at = 0
      return {
        readEntries: (ok) => {
          const batch = children.slice(at, at + batchSize)
          at += batchSize
          ok(batch)
        },
      }
    },
  }
}

describe('isAudioName', () => {
  it('keeps .wav, .mp3 and .m4a in any case', () => {
    for (const name of ['a.wav', 'a.mp3', 'a.m4a', 'A.WAV', 'b.Mp3', 'dir/c.m4a']) expect(isAudioName(name), name).toBe(true)
  })

  it('skips everything else', () => {
    for (const name of ['notes.txt', 'a.mp4', 'a.flac', 'mp3', 'a.mp3.zip', '.DS_Store', '._a.mp3', '.hidden.wav', 'dir/._a.wav'])
      expect(isAudioName(name), name).toBe(false)
  })
})

describe('folder drops', () => {
  it('walks nested folders across read batches and skips non-audio', async () => {
    const tree = [
      fileEntry('top.mp3'),
      fileEntry('readme.txt'),
      folder([
        fileEntry('a.wav'), fileEntry('b.m4a'), fileEntry('.DS_Store'), fileEntry('c.pdf'), fileEntry('d.mp3'),
        folder([fileEntry('deep.WAV'), fileEntry('._deep.wav')]),
      ]),
    ]
    const names = keepAudio(await filesFromEntries(tree)).map((f) => f.name)
    expect(names).toEqual(['top.mp3', 'a.wav', 'b.m4a', 'd.mp3', 'deep.WAV'])
  })

  it('gives nothing for an empty folder', async () => {
    expect(await filesFromEntries([folder([])])).toEqual([])
  })

  it('finds more than one batch of files', async () => {
    const many = Array.from({ length: 250 }, (_, i) => fileEntry(`c${i}.mp3`))
    expect(await filesFromEntries([folder(many, 100)])).toHaveLength(250)
  })
})
