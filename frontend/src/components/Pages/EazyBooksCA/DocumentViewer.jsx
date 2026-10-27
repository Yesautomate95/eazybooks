import React, { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { X, Loader2, Download, Maximize2, Minimize2, ZoomIn, ZoomOut, RotateCw } from 'lucide-react';
const Portal = ({ children }) => {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  return mounted ? createPortal(children, document.body) : null;
};

export default function DocumentViewer({ document, onClose }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [zoom, setZoom] = useState(100);
  const [rotation, setRotation] = useState(0);

  const handleFullscreenToggle = useCallback(() => {
    setIsFullscreen(!isFullscreen);
  }, [isFullscreen]);

  const handleZoomIn = useCallback(() => {
    setZoom(prev => Math.min(prev + 10, 200));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom(prev => Math.max(prev - 10, 50));
  }, []);

  const handleRotate = useCallback(() => {
    setRotation(prev => (prev + 90) % 360);
  }, []);

  useEffect(() => {
    if (!document?.file_link) {
      setError('No document link provided');
      setLoading(false);
      return;
    }
    setLoading(false);
  }, [document]);

  return (
    <Portal>
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className={`bg-white rounded-lg flex flex-col ${isFullscreen ? 'fixed inset-4' : 'w-11/12 h-5/6'}`}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">{document.file_name}</h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleFullscreenToggle}
              className="p-1.5 text-gray-600 hover:bg-gray-100 rounded-full transition-colors"
              title={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
            >
              {isFullscreen ? <Minimize2 className="h-5 w-5" /> : <Maximize2 className="h-5 w-5" />}
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-gray-600 hover:bg-gray-100 rounded-full transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Document Viewer */}
        <div className="flex-1 relative group">
          {/* Zoom and Rotate Controls */}
          <div className="absolute top-2 left-1/2 transform -translate-x-1/2 flex items-center space-x-2 bg-white px-4 py-2 rounded-full shadow-lg z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none group-hover:pointer-events-auto">
            <button
              onClick={handleZoomOut}
              className="p-1.5 text-black hover:bg-gray-100 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Zoom Out"
              disabled={zoom <= 50}
            >
              <ZoomOut className="h-4 w-4" />
            </button>
            <span className="text-sm font-medium text-black min-w-[3rem] text-center">
              {zoom}%
            </span>
            <button
              onClick={handleZoomIn}
              className="p-1.5 text-black hover:bg-gray-100 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Zoom In"
              disabled={zoom >= 200}
            >
              <ZoomIn className="h-4 w-4" />
            </button>
            <div className="w-px h-4 bg-gray-300 mx-2"></div>
            <button
              onClick={handleRotate}
              className="p-1.5 text-black hover:bg-gray-100 rounded-full transition-colors"
              title="Rotate"
            >
              <RotateCw className="h-4 w-4" />
            </button>
          </div>

          {loading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-50 z-10">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600 mb-2" />
              <span className="text-sm font-medium text-gray-600">Loading document...</span>
            </div>
          )}

          <div className="absolute inset-0 p-4 overflow-auto">
            <div
              className="w-full h-full flex items-center justify-center"
              style={{
                transform: `scale(${zoom / 100}) rotate(${rotation}deg)`,
                transformOrigin: 'center center',
                transition: 'transform 0.3s ease'
              }}
            >
              <iframe
                src={document.file_link || ''}
                className="w-full h-full border-0 bg-white rounded-lg shadow-lg"
                title={document.file_name}
                onLoad={() => setLoading(false)}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
    </Portal>
  );
}
