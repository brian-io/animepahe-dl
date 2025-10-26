'use client'

import { useState, useEffect, FormEvent } from 'react'
import { Search, Download, Settings, Folder, Play, AlertCircle, CheckCircle } from 'lucide-react'

interface Anime {
  title: string
  session: string
}

interface DownloadSettings {
  startEpisode: number
  endEpisode: string
  quality: number
  preferDub: boolean
  outputDir: string
}

interface DownloadProgress {
  current: number
  total: number
  episode: number
}

interface Log {
  type: 'error' | 'success' | 'info'
  message: string
}

interface Download {
  title: string
  episode: number
  quality: number
  size: string
  timestamp: string
}

interface StreamData {
  type: 'progress' | 'log' | 'complete'
  progress?: DownloadProgress
  log?: Log
}

export default function Home() {
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [searchResults, setSearchResults] = useState<Anime[]>([])
  const [selectedAnime, setSelectedAnime] = useState<Anime | null>(null)
  const [downloadSettings, setDownloadSettings] = useState<DownloadSettings>({
    startEpisode: 1,
    endEpisode: '',
    quality: 1080,
    preferDub: false,
    outputDir: 'downloads'
  })
  const [isSearching, setIsSearching] = useState<boolean>(false)
  const [isDownloading, setIsDownloading] = useState<boolean>(false)
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null)
  const [logs, setLogs] = useState<Log[]>([])
  const [downloads, setDownloads] = useState<Download[]>([])

  // Fetch current downloads on component mount
  useEffect(() => {
    fetchDownloads()
  }, [])

  const fetchDownloads = async (): Promise<void> => {
    try {
      const response = await fetch('/api/downloads')
      const data: { downloads?: Download[] } = await response.json()
      setDownloads(data.downloads || [])
    } catch (error) {
      console.error('Failed to fetch downloads:', error)
    }
  }

  const handleSearch = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault()
    if (!searchQuery.trim()) return

    setIsSearching(true)
    setSearchResults([])

    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query: searchQuery }),
      })

      const data: { 
        success: boolean;
        results?: Record<string, string>; // title -> session ID
        error?: string 
      } = await response.json()
      
      if (data.success && data.results) {
        const formattedResults: Anime[] = Object.entries(data.results).map(([title, session]) => ({
          title,
          session
        }))
        setSearchResults(formattedResults)
      } else {
        setLogs(prev => [...prev, { type: 'error', message: data.error || 'Search failed' }])
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      setLogs(prev => [...prev, { type: 'error', message: 'Failed to search: ' + errorMessage }])
    } finally {
      setIsSearching(false)
    }
  }

  const handleDownload = async (): Promise<void> => {
    if (!selectedAnime) return

    setIsDownloading(true)
    setDownloadProgress({ current: 0, total: 0, episode: 0 })
    setLogs([])

    try {
      const response = await fetch('/api/download', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          anime: selectedAnime,
          settings: downloadSettings
        }),
      })

      if (!response.ok) {
        throw new Error('Download request failed')
      }

      // Handle streaming response for real-time updates
      if (!response.body) {
        throw new Error('Response body is null')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.trim()) {
            try {
              const data: StreamData = JSON.parse(line)
              
              if (data.type === 'progress' && data.progress) {
                setDownloadProgress(data.progress)
              } else if (data.type === 'log' && data.log) {
                setLogs(prev => [...prev, data.log as Log])
              } else if (data.type === 'complete') {
                setDownloadProgress(null)
                setLogs(prev => [...prev, { type: 'success', message: 'Download completed!' }])
                fetchDownloads() // Refresh downloads list
              }
            // eslint-disable-next-line @typescript-eslint/no-unused-vars
            } catch (e) {
              // Ignore JSON parse errors for partial chunks
            }
          }
        }
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      setLogs(prev => [...prev, { type: 'error', message: 'Download failed: ' + errorMessage }])
    } finally {
      setIsDownloading(false)
    }
  }

  const handleCancelDownload = async (): Promise<void> => {
    try {
      await fetch('/api/download/cancel', { method: 'POST' })
      setIsDownloading(false)
      setDownloadProgress(null)
      setLogs(prev => [...prev, { type: 'info', message: 'Download cancelled' }])
    } catch (error) {
      console.error('Failed to cancel download:', error)
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-6xl">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Anime Downloader</h1>
        <p className="text-gray-600">Search and download anime episodes from AnimePahe</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Search Section */}
        <div className="space-y-6">
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4 flex items-center">
              <Search className="mr-2 h-5 w-5" />
              Search Anime
            </h2>
            
            <form onSubmit={handleSearch} className="space-y-4">
              <div>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Enter anime title..."
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  disabled={isSearching}
                />
              </div>
              
              <button
                type="submit"
                disabled={isSearching || !searchQuery.trim()}
                className="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
              >
                {isSearching ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2"></div>
                    Searching...
                  </>
                ) : (
                  <>
                    <Search className="mr-2 h-4 w-4" />
                    Search
                  </>
                )}
              </button>
            </form>
          </div>

          {/* Search Results */}
          {searchResults.length > 0 && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h3 className="text-lg font-semibold mb-4">Search Results</h3>
              <div className="space-y-2">
                {searchResults.map((anime, index) => (
                  <div
                    key={index}
                    onClick={() => setSelectedAnime(anime)}
                    className={`p-3 rounded-lg cursor-pointer transition-colors ${
                      selectedAnime?.title === anime.title
                        ? 'bg-blue-100 border-blue-300'
                        : 'bg-gray-50 hover:bg-gray-100'
                    } border`}
                  >
                    <div className="font-medium text-gray-900">{anime.title}</div>
                    <div className="text-sm text-gray-600">Session: {anime.session}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Download Section */}
        <div className="space-y-6">
          {selectedAnime && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-4 flex items-center">
                <Settings className="mr-2 h-5 w-5" />
                Download Settings
              </h2>
              
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Selected Anime
                  </label>
                  <div className="p-3 bg-gray-50 rounded-lg">
                    <div className="font-medium">{selectedAnime.title}</div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Start Episode
                    </label>
                    <input
                      type="number"
                      value={downloadSettings.startEpisode}
                      onChange={(e) => setDownloadSettings(prev => ({
                        ...prev,
                        startEpisode: parseInt(e.target.value) || 1
                      }))}
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      End Episode (optional)
                    </label>
                    <input
                      type="number"
                      value={downloadSettings.endEpisode}
                      onChange={(e) => setDownloadSettings(prev => ({
                        ...prev,
                        endEpisode: e.target.value
                      }))}
                      min="1"
                      placeholder="All"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Quality
                  </label>
                  <select
                    value={downloadSettings.quality}
                    onChange={(e) => setDownloadSettings(prev => ({
                      ...prev,
                      quality: parseInt(e.target.value)
                    }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                  >
                    <option value={1080}>1080p</option>
                    <option value={720}>720p</option>
                    <option value={480}>480p</option>
                    <option value={360}>360p</option>
                  </select>
                </div>

                <div>
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={downloadSettings.preferDub}
                      onChange={(e) => setDownloadSettings(prev => ({
                        ...prev,
                        preferDub: e.target.checked
                      }))}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="ml-2 text-sm text-gray-700">Prefer dubbed version</span>
                  </label>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Output Directory
                  </label>
                  <input
                    type="text"
                    value={downloadSettings.outputDir}
                    onChange={(e) => setDownloadSettings(prev => ({
                      ...prev,
                      outputDir: e.target.value
                    }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div className="pt-4">
                  {!isDownloading ? (
                    <button
                      onClick={handleDownload}
                      className="w-full bg-green-600 text-white py-3 px-4 rounded-lg hover:bg-green-700 flex items-center justify-center font-medium"
                    >
                      <Download className="mr-2 h-5 w-5" />
                      Start Download
                    </button>
                  ) : (
                    <button
                      onClick={handleCancelDownload}
                      className="w-full bg-red-600 text-white py-3 px-4 rounded-lg hover:bg-red-700 flex items-center justify-center font-medium"
                    >
                      Cancel Download
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Download Progress */}
          {downloadProgress && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h3 className="text-lg font-semibold mb-4">Download Progress</h3>
              <div className="space-y-3">
                <div>
                  <div className="flex justify-between text-sm text-gray-600 mb-1">
                    <span>Episode {downloadProgress.episode}</span>
                    <span>{downloadProgress.current}/{downloadProgress.total}</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                      style={{
                        width: downloadProgress.total > 0 
                          ? `${(downloadProgress.current / downloadProgress.total) * 100}%` 
                          : '0%'
                      }}
                    ></div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Logs */}
          {logs.length > 0 && (
            <div className="bg-white rounded-lg shadow-md p-6">
              <h3 className="text-lg font-semibold mb-4">Logs</h3>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {logs.map((log, index) => (
                  <div
                    key={index}
                    className={`flex items-start space-x-2 p-2 rounded text-sm ${
                      log.type === 'error' ? 'bg-red-50 text-red-700' :
                      log.type === 'success' ? 'bg-green-50 text-green-700' :
                      'bg-blue-50 text-blue-700'
                    }`}
                  >
                    {log.type === 'error' ? (
                      <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                    ) : log.type === 'success' ? (
                      <CheckCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                    ) : (
                      <div className="h-4 w-4 mt-0.5 flex-shrink-0"></div>
                    )}
                    <span>{log.message}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Downloads List */}
      {downloads.length > 0 && (
        <div className="mt-8 bg-white rounded-lg shadow-md p-6">
          <h2 className="text-xl font-semibold mb-4 flex items-center">
            <Folder className="mr-2 h-5 w-5" />
            Recent Downloads
          </h2>
          <div className="space-y-3">
            {downloads.map((download, index) => (
              <div key={index} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center space-x-3">
                  <Play className="h-5 w-5 text-green-600" />
                  <div>
                    <div className="font-medium">{download.title}</div>
                    <div className="text-sm text-gray-600">
                      Episode {download.episode} • {download.quality}p • {download.size}
                    </div>
                  </div>
                </div>
                <div className="text-sm text-gray-500">
                  {new Date(download.timestamp).toLocaleDateString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}