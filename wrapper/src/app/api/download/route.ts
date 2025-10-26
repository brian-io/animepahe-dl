import { NextResponse, NextRequest } from 'next/server'
import { spawn, ChildProcessWithoutNullStreams } from 'child_process'
import path from 'path'

let currentDownload: ChildProcessWithoutNullStreams | null = null

type AnimeInfo = {
  title: string
}

type DownloadSettings = {
  startEpisode: number
  endEpisode?: number
  quality: number
  outputDir?: string
  preferDub: boolean
}

export async function POST(request: NextRequest) {
  try {
    const { anime, settings }: { anime: AnimeInfo; settings: DownloadSettings } = await request.json()

    if (currentDownload) {
      return NextResponse.json({ success: false, error: 'Download already in progress' })
    }
    const downloadScript = path.join(__dirname, '../../../../../..', 'pahe-dl.py')

    const args: string[] = [
      downloadScript,
      '--name', anime.title,
      '--start', settings.startEpisode.toString(),
      '--quality', settings.quality.toString(),
   ]

    if (settings.endEpisode !== undefined) {
      args.push('--end', settings.endEpisode.toString())
    }

    if (settings.preferDub) {
      args.push('--dub')
    }

    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        currentDownload = spawn('python3', args)

        currentDownload.stdout.on('data', (data: Buffer) => {
          const output = data.toString()
          const lines = output.split('\n')

          for (const line of lines) {
            if (line.trim()) {
              const logData = JSON.stringify({
                type: 'log',
                log: { type: 'info', message: line.trim() }
              }) + '\n'
              controller.enqueue(encoder.encode(logData))
            }
          }
        })

        currentDownload.stderr.on('data', (data: Buffer) => {
          const errorData = JSON.stringify({
            type: 'log',
            log: { type: 'error', message: data.toString().trim() }
          }) + '\n'
          controller.enqueue(encoder.encode(errorData))
        })

        currentDownload.on('close', (code: number) => {
          const completeData = JSON.stringify({
            type: 'complete',
            success: code === 0
          }) + '\n'
          controller.enqueue(encoder.encode(completeData))
          controller.close()
          currentDownload = null
        })
      },

      cancel() {
        if (currentDownload) {
          currentDownload.kill()
          currentDownload = null
        }
      }
    })

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/plain',
        'Transfer-Encoding': 'chunked',
      },
    })
  } catch (error: unknown) {
    if (error instanceof Error){
      return NextResponse.json({ success: false, error: error.message });
    }
  }
}
