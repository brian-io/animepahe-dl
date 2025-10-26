import { NextResponse } from 'next/server'

export async function POST() {
  // This would reference the currentDownload from the download route
  // In a real implementation, you'd need to manage this state properly
  return NextResponse.json({ success: true })
}