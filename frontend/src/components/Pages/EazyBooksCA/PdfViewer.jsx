import React, { useState, useRef, useCallback, useEffect } from "react";
import { ZoomIn, ZoomOut, ChevronLeft, ChevronRight, RefreshCw, ExternalLink, Maximize } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Initialize pdfjs worker - use local copy for reliability (no CDN/CORS issues)
pdfjs.GlobalWorkerOptions.workerSrc = `/pdf.worker.min.mjs`;

/**
 * Shared PDF Viewer component with auto-fit, multi-page continuous scroll,
 * zoom controls, and high-DPI support.
 *
 * Props:
 *  - pdfData: base64 data URL string (e.g. "data:application/pdf;base64,...")
 *  - className: optional wrapper class overrides
 */
function PdfViewer({ pdfData, className = "" }) {
  const [numPages, setNumPages] = useState(null);
  const [pdfError, setPdfError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [containerWidth, setContainerWidth] = useState(null);
  const [useIframeFallback, setUseIframeFallback] = useState(false);
  const [zoomMode, setZoomMode] = useState("fit-width"); // "fit-width" | number (custom scale)
  const [customScale, setCustomScale] = useState(1);
  const containerRef = useRef(null);
  const scrollRef = useRef(null);

  // Measure container width with ResizeObserver
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const measure = () => {
      const width = container.clientWidth - 32; // 16px padding each side
      if (width > 0) setContainerWidth(width);
    };

    measure();

    const observer = new ResizeObserver(() => {
      measure();
    });
    observer.observe(container);

    return () => observer.disconnect();
  }, []);

  // Compute the width to pass to react-pdf <Page>
  const pageWidth = zoomMode === "fit-width" ? containerWidth : null;
  const pageScale = zoomMode === "fit-width" ? undefined : customScale;

  // Displayed zoom percentage (approximate)
  const [displayZoom, setDisplayZoom] = useState(100);

  const onDocumentLoadSuccess = useCallback(({ numPages: total }) => {
    setNumPages(total);
    setPdfError(null);
    setIsLoading(false);
    setUseIframeFallback(false);
  }, []);

  const onDocumentLoadError = useCallback((error) => {
    console.error("PdfViewer: Document load error:", error);
    setPdfError("Failed to load with react-pdf, trying iframe...");
    setIsLoading(false);
    setUseIframeFallback(true);
  }, []);

  // Track rendered page dimensions to compute display zoom %
  const onFirstPageLoad = useCallback((page) => {
    const { originalWidth } = page;
    const actualWidth = originalWidth || page.width;
    if (actualWidth && containerWidth) {
      const ratio = containerWidth / actualWidth;
      setDisplayZoom(Math.round(ratio * 100));
      // Initialize custom scale to fit-width equivalent
      setCustomScale(ratio);
    }
  }, [containerWidth]);

  // Zoom handlers
  const zoomIn = () => {
    const newScale = zoomMode === "fit-width" ? customScale * 1.2 : customScale * 1.2;
    setCustomScale(Math.min(newScale, 4));
    setZoomMode("custom");
    setDisplayZoom(Math.round(Math.min(newScale, 4) * 100));
  };

  const zoomOut = () => {
    const newScale = zoomMode === "fit-width" ? customScale * 0.8 : customScale * 0.8;
    setCustomScale(Math.max(newScale, 0.3));
    setZoomMode("custom");
    setDisplayZoom(Math.round(Math.max(newScale, 0.3) * 100));
  };

  const fitToWidth = () => {
    setZoomMode("fit-width");
    if (containerWidth) {
      // displayZoom will be recalculated on next page render
    }
  };

  // Ctrl/Cmd + wheel zooms the PDF (and not the browser page).
  // Also handles trackpad pinch — browsers fire wheel with ctrlKey=true.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleWheel = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      // Work from the current effective scale (fit-width uses customScale that's synced on first-page load).
      const base = customScale || 1;
      // Normalize delta across mouse wheel vs trackpad pinch.
      const step = e.deltaY > 0 ? 0.9 : 1.1;
      const next = Math.min(4, Math.max(0.3, base * step));
      setCustomScale(next);
      setZoomMode("custom");
      setDisplayZoom(Math.round(next * 100));
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [customScale]);

  // Keyboard shortcuts for zoom
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
      if (e.ctrlKey && e.key === "=") { e.preventDefault(); zoomIn(); }
      if (e.ctrlKey && e.key === "-") { e.preventDefault(); zoomOut(); }
      if (e.ctrlKey && e.key === "0") { e.preventDefault(); fitToWidth(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  });

  if (!pdfData) {
    return (
      <div className={`flex items-center justify-center h-full text-gray-500 ${className}`}>
        No PDF available
      </div>
    );
  }

  // Use iframe fallback if react-pdf failed
  if (useIframeFallback) {
    return (
      <div ref={containerRef} className={`relative flex flex-col h-full bg-white rounded-lg border ${className}`}>
        <div className="flex-1 overflow-hidden">
          <iframe
            src={`${pdfData}#view=FitH`}
            className="w-full h-full border-0"
            title="PDF Preview"
          />
        </div>
        <div className="flex items-center justify-center gap-2 p-2 border-t bg-white rounded-b-lg">
          <span className="text-xs text-gray-500">Viewing in embedded mode</span>
          <button
            onClick={() => {
              setUseIframeFallback(false);
              setPdfError(null);
              setIsLoading(true);
            }}
            className="p-1.5 rounded-full hover:bg-gray-100 border border-gray-200"
            title="Retry with react-pdf"
          >
            <RefreshCw className="h-4 w-4 text-gray-600" />
          </button>
          <button
            onClick={() => window.open(pdfData, '_blank')}
            className="p-1.5 rounded-full hover:bg-gray-100 border border-gray-200"
            title="Open in new tab"
          >
            <ExternalLink className="h-4 w-4 text-gray-600" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className={`relative flex flex-col h-full bg-gray-700 rounded-lg overflow-hidden ${className}`}>
      {/* PDF content area — continuous scroll for all pages */}
      <div ref={scrollRef} className="flex-1 overflow-auto" style={{ backgroundColor: "#525659" }}>
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white bg-opacity-75 z-20">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
          </div>
        )}

        {pdfError && !useIframeFallback ? (
          <div className="flex flex-col items-center justify-center h-full p-4 gap-3">
            <p className="text-red-400 text-sm">{pdfError}</p>
            <button
              onClick={() => setUseIframeFallback(true)}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              Try Embedded View
            </button>
          </div>
        ) : (
          <Document
            file={pdfData}
            onLoadSuccess={onDocumentLoadSuccess}
            onLoadError={onDocumentLoadError}
            loading={
              <div className="flex items-center justify-center h-64">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
              </div>
            }
            className="flex flex-col items-center py-4 gap-4"
          >
            {numPages && Array.from({ length: numPages }, (_, i) => (
              <Page
                key={`page-${i + 1}`}
                pageNumber={i + 1}
                width={pageWidth || undefined}
                scale={pageScale}
                renderTextLayer={false}
                renderAnnotationLayer={false}
                onLoadSuccess={i === 0 ? onFirstPageLoad : undefined}
                className="shadow-lg"
                loading={
                  <div className="flex items-center justify-center p-8">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-white" />
                  </div>
                }
              />
            ))}
          </Document>
        )}
      </div>

      {/* Controls bar */}
      <div className="flex items-center justify-center gap-1.5 px-3 py-2 bg-gray-800 border-t border-gray-600 shrink-0">
        {/* Zoom out */}
        <button
          onClick={zoomOut}
          className="p-1.5 rounded hover:bg-gray-600 transition-colors"
          title="Zoom Out (Ctrl+-)"
        >
          <ZoomOut className="h-4 w-4 text-gray-300" />
        </button>

        {/* Zoom display */}
        <span className="text-xs font-medium text-gray-300 w-12 text-center select-none">
          {zoomMode === "fit-width" ? "Fit" : `${displayZoom}%`}
        </span>

        {/* Zoom in */}
        <button
          onClick={zoomIn}
          className="p-1.5 rounded hover:bg-gray-600 transition-colors"
          title="Zoom In (Ctrl+=)"
        >
          <ZoomIn className="h-4 w-4 text-gray-300" />
        </button>

        {/* Fit to width */}
        <button
          onClick={fitToWidth}
          className={`p-1.5 rounded transition-colors ${zoomMode === "fit-width" ? "bg-gray-600" : "hover:bg-gray-600"}`}
          title="Fit to Width (Ctrl+0)"
        >
          <Maximize className="h-4 w-4 text-gray-300" />
        </button>

        <div className="w-px h-5 bg-gray-600 mx-1" />

        {/* Page count */}
        {numPages && (
          <span className="text-xs text-gray-400 select-none">
            {numPages} {numPages === 1 ? "page" : "pages"}
          </span>
        )}

        <div className="w-px h-5 bg-gray-600 mx-1" />

        {/* Open in new tab */}
        <button
          onClick={() => window.open(pdfData, '_blank')}
          className="p-1.5 rounded hover:bg-gray-600 transition-colors"
          title="Open in new tab"
        >
          <ExternalLink className="h-4 w-4 text-gray-300" />
        </button>
      </div>
    </div>
  );
}

export default PdfViewer;
