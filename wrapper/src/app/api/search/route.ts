import { NextResponse, NextRequest } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'

export async function POST(request: NextRequest) {
  try {
    const { query }: { query: string } = await request.json()

    if (!query) {
      return NextResponse.json({ success: false, error: 'Query is required' })
    }

    return new Promise<Response>((resolve) => {
      const downloadScript = path.join(__dirname, '../../../../../..', 'pahe-dl.py')
        
      const python = spawn('python3', [downloadScript, '--search-only', '--name', query])

      let stdout = ''
      let stderr = ''

      python.stdout.on('data', (data: Buffer) => {
        stdout += data.toString()
      })

      python.stderr.on('data', (data: Buffer) => {
        stderr += data.toString()
      })

      python.on('close', (code: number) => {
        if (code === 0) {
          try {
            const results = JSON.parse(stdout)
            resolve(NextResponse.json({ success: true, results }))
          } catch (error: unknown) {
            if (error instanceof Error){
                resolve(NextResponse.json({ success: false, error: 'Failed to parse search results' }));
            }
          }
        } else {
          resolve(NextResponse.json({ success: false, error: stderr || 'Search failed' }))
        }
      })
    })
  } catch (error: unknown) {
        if (error instanceof Error){
        return NextResponse.json({ success: false, error: error.message });
        }
    }
}
