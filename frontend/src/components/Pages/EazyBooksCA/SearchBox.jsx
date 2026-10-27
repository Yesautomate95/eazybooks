import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { filesApi } from "../../../services/api";
import DocumentViewer from "./DocumentViewer";

const SearchBox = () => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedDocument, setSelectedDocument] = useState(null);
  const [isFocused, setIsFocused] = useState(false);
  const dropdownRef = useRef(null);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  // Group results by type
  const groupedResults = results.reduce((acc, doc) => {
    const type = doc.folder_id ? 'vault' : 'other';
    if (!acc[type]) acc[type] = [];
    acc[type].push(doc);
    return acc;
  }, {});

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      // Clear any pending search timeout when component unmounts
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, []);

  const searchDocuments = async (searchQuery) => {
    if (!searchQuery.trim()) {
      setResults([]);
      return;
    }

    setIsLoading(true);
    try {
      const result = await filesApi.searchDocuments(searchQuery);
      setResults(result.documents);
      setShowDropdown(true);
    } catch (error) {
      console.error('Search error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // Store timeout ID in a ref to properly handle cleanup
  const searchTimeoutRef = useRef(null);

  const handleSearch = (e) => {
    const value = e.target.value;
    setQuery(value);
    
    // Clear any existing timeout
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    // Only search if there's actual text
    if (value.trim()) {
      // Set new timeout and store its ID
      searchTimeoutRef.current = setTimeout(() => {
        searchDocuments(value);
      }, 300);
    } else {
      // If search is empty, clear results immediately
      setResults([]);
      setShowDropdown(false);
    }
  };

  const handleDocumentClick = (doc) => {
    setShowDropdown(false);
    setQuery('');
    if (doc.file_link) {
      setSelectedDocument(doc);
    }
  };

  const handleCloseViewer = () => {
    setSelectedDocument(null);
  };

  return (
    <>
      <div className="relative w-full">
        <svg
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-gilt-faint"
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleSearch}
          onFocus={() => {
            setShowDropdown(true);
            setIsFocused(true);
          }}
          placeholder="Search documents…"
          className="w-full pl-8 pr-8 h-8 bg-gilt-sunken text-gilt-ink placeholder:text-gilt-faint border border-gilt-hairline focus:outline-none focus:border-gilt-brass rounded-md text-sm transition-colors duration-150"
        />
        <kbd className="hidden md:inline-flex absolute right-2 top-1/2 -translate-y-1/2 items-center h-5 px-1.5 rounded border border-gilt-hairline bg-gilt-card text-2xs font-mono font-medium text-gilt-faint">
          ⌘K
        </kbd>
        {isLoading && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            <div className="animate-spin rounded-full h-3.5 w-3.5 border-2 border-gilt-hairline border-t-gilt-brass"></div>
          </div>
        )}
      </div>

      {showDropdown && createPortal(
        <div className="fixed inset-0 z-[9999]" ref={dropdownRef}>
          <div
            className="fixed inset-0 bg-gilt-ink/40"
            onClick={() => setShowDropdown(false)}
          />
          <div className="fixed top-[20vh] left-1/2 -translate-x-1/2 w-full max-w-2xl px-4">
            <div className="bg-gilt-card rounded-xl overflow-hidden border border-gilt-hairline">
              <div className="p-2 border-b border-gilt-hairline">
                <input
                  autoFocus
                  type="text"
                  value={query}
                  onChange={handleSearch}
                  placeholder="Type a command or search..."
                  className="w-full px-3 py-1.5 bg-transparent text-gilt-ink placeholder:text-gilt-faint border-0 focus:outline-none text-sm"
                />
              </div>
          {Object.entries(groupedResults).map(([type, docs]) => (
            <div key={type} className="py-1">
              {docs.length > 0 && (
                <>
                  <div className="px-4 py-1 font-mono text-[10px] tracking-[0.14em] text-gilt-soft uppercase">{type}</div>
                  {docs.map((doc) => (
                    <div
                      key={doc.file_id}
                      onClick={() => handleDocumentClick(doc)}
                      className="group px-4 py-1.5 border-b border-gilt-hairline/60 last:border-b-0 hover:bg-gilt-sunken cursor-pointer flex items-center transition-colors duration-150"
                    >
                      <div className="text-gilt-faint group-hover:text-gilt-brass transition-colors duration-150 flex-shrink-0 mr-3">
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <span className="text-sm font-mono text-gilt-ink">{doc.document_id}</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          ))}
          {results.length === 0 && query && !isLoading && (
            <div className="px-4 py-8 text-center text-gilt-soft">
              No documents found
            </div>
          )}
            </div>
          </div>
        </div>,
        document.body
      )}
      {selectedDocument && (
        <DocumentViewer
          document={selectedDocument}
          onClose={handleCloseViewer}
        />
      )}
    </>
  );
};

export default SearchBox;
