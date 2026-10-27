import React, { useState } from "react";
import {
  FaFilePdf,
  FaFileWord,
  FaFileImage,
  FaFileExcel,
  FaFileAlt,
  FaFolder,
  FaLock,
  FaUser,
  FaEye,
  FaTrash,
} from "react-icons/fa";
import { filesApi, clientsApi } from "../../../services/api";

/**
 * Returns the appropriate icon based on the file MIME type.
 */
function getFileIcon(fileType) {
  if(!fileType) {
      return <FaFileAlt className="text-xl text-gilt-soft" />;
  }
  if (fileType === "Folder" || fileType.toLowerCase() === "folder") {
    return <FaFolder className="text-xl text-gilt-soft" />;
  }
  switch (fileType) {
    case "application/pdf":
      return <FaFilePdf className="text-xl text-gilt-soft" />;
    case "application/msword":
    case "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
      return <FaFileWord className="text-xl text-gilt-soft" />;
    case "image/png":
    case "image/jpeg":
    case "image/jpg":
    case "image/gif":
      return <FaFileImage className="text-xl text-gilt-soft" />;
    case "application/vnd.ms-excel":
    case "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
      return <FaFileExcel className="text-xl text-gilt-soft" />;
    default:
      return <FaFileAlt className="text-xl text-gilt-soft" />;
  }
}

/**
 * Returns a user-friendly file type label based on the file MIME type.
 */
function getFriendlyFileType(fileType) {
  if(!fileType) {
      return "Unknown";
  }
  if (fileType === "Folder" || fileType.toLowerCase() === "folder") {
    return "Folder";
  }
  switch (fileType) {
    case "application/pdf":
      return "PDF";
    case "application/msword":
    case "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
      return "Word";
    case "image/png":
    case "image/jpeg":
    case "image/jpg":
    case "image/gif":
      return "Image";
    case "application/vnd.ms-excel":
    case "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
      return "Excel";
    default:
      return "Unknown";
  }
}

/**
 * Formats a file size (provided as a number in kilobytes) to either "X KB" (if ≤ 100KB)
 * or as "Y MB" (if > 100KB). Uses 1024 as the conversion factor.
 */
function formatFileSize(sizeInKB) {
  if (sizeInKB <= 100) {
    return `${sizeInKB} KB`;
  } else {
    return `${(sizeInKB / 1024).toFixed(2)} MB`;
  }
}

/**
 * Returns the shared column content with an icon:
 * - Lock icon + "Only me" if shared is empty or "Only me"
 * - User icon + text otherwise (e.g., "4 people")
 */
function renderSharedValue(shared) {
  const trimmed = shared?.trim().toLowerCase();

  // Default or "Only me"
  if (!trimmed || trimmed === "only me") {
    return (
      <span className="flex items-center gap-1">
        <FaLock className="text-gilt-faint" />
        <span>Only me</span>
      </span>
    );
  }

  // Otherwise: show user icon + text
  return (
    <span className="flex items-center gap-1">
      <FaUser className="text-gilt-faint" />
      <span>{shared}</span>
    </span>
  );
}

/**
 * Formats the date in a user-friendly, elegant format.
 * Example: "Mar 20, 2025, 4:45 PM"
 */
function formatDate(dateString) {
  if (!dateString) return "—";
  const date = new Date(dateString);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export default function ClientFilesTable({ files = [], onFileClick, onFileDelete, onRefresh }) {
  const [activeTab, setActiveTab] = useState("View all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [isDeleting, setIsDeleting] = useState(false);

  // Handle tab switching
  const handleTabClick = (tab) => {
    setActiveTab(tab);
    // Optionally clear selections when switching tabs
    setSelectedFiles([]);
  };

  // Handle search
  const handleSearch = (e) => {
    setSearchQuery(e.target.value);
  };

  // Select/deselect all in the current filtered set
  const handleSelectAll = (e, filteredFiles) => {
    if (e.target.checked) {
      const allIds = filteredFiles.map((f) => f.file_id);
      setSelectedFiles(allIds);
    } else {
      setSelectedFiles([]);
    }
  };

  // Toggle selection of a single file
  const handleSelectFile = (fileId) => {
    setSelectedFiles((prev) =>
      prev.includes(fileId) ? prev.filter((id) => id !== fileId) : [...prev, fileId]
    );
  };

  // Filter files based on active tab and search query using MIME types
  const filteredFiles = files.filter((file) => {
    // Tab-based filtering using MIME types
    if (activeTab === "Documents" && !(
      file.file_type === "application/msword" ||
      file.file_type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )) {
      return false;
    }
    if (activeTab === "Media files" && !(
      file.file_type === "image/png" ||
      file.file_type === "image/jpeg" ||
      file.file_type === "image/jpg" ||
      file.file_type === "image/gif"
    )) {
      return false;
    }
    if (activeTab === "PDFs" && file.file_type !== "application/pdf") {
      return false;
    }

    // Search-based filtering
    if (
      searchQuery &&
      !file.file_name.toLowerCase().includes(searchQuery.toLowerCase())
    ) {
      return false;
    }

    return true;
  });

  const handleDeleteFile = async (fileId) => {
    if (!window.confirm('Are you sure you want to delete this file?')) {
      return;
    }

    setIsDeleting(true);
    try {
      await filesApi.deleteFile(fileId);

      // Clear both lockers and clients cache
      localStorage.removeItem('lockers');
      localStorage.removeItem('lockers_timestamp');
      localStorage.removeItem('clients');
      localStorage.removeItem('clients_timestamp');

      // Refresh both lockers and clients data
      try {
        await Promise.all([
          clientsApi.getLockersWithDetails(),
          clientsApi.getClientsWithDetails()
        ]);
      } catch (err) {
        console.error("Error refreshing data:", err);
      }
      
      // Call the parent's onFileDelete handler to update the UI
      if (onFileDelete) {
        onFileDelete(fileId);
      }

      // Call the refresh callback to update parent state
      if (onRefresh) {
        onRefresh();
      }
    } catch (error) {
      console.error('Error deleting file:', error);
      alert('Failed to delete file. Please try again.');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="text-gilt-ink mb-6 rounded-md w-full">
      {/* Title */}
      <h1 className="text-2xl font-bold text-gilt-ink mb-6">All files</h1>

      {/* Tabs + Search & Filters Row */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-6">
        {/* Tabs */}
        <div className="flex items-center gap-2">
          {["View all", "Documents", "Media files", "PDFs"].map((tab) => (
            <button
              key={tab}
              onClick={() => handleTabClick(tab)}
              className={`px-3 py-1 rounded-md text-sm transition-colors ${
                activeTab === tab
                  ? "bg-gilt-sunken text-gilt-ink font-medium"
                  : "text-gilt-soft hover:bg-gilt-sunken hover:text-gilt-ink"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Search & Filters */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Search"
            value={searchQuery}
            onChange={handleSearch}
            className="bg-gilt-card border border-gilt-hairline rounded-md px-3 py-2 text-sm text-gilt-ink placeholder:text-gilt-faint focus:outline-none focus:border-gilt-brass transition-colors"
          />
          <button className="border border-gilt-hairline rounded-md px-3 py-2 text-sm bg-gilt-card text-gilt-soft hover:border-gilt-brass hover:text-gilt-brass transition-colors">
            Filters
          </button>
        </div>
      </div>

      {/* Scrollable Table Container with dynamic height */}
      <div className="border border-gilt-hairline bg-gilt-card rounded-md overflow-auto max-h-96">
        <table className="w-full text-left min-w-[700px]">
          {/* Table Header */}
          <thead className="bg-gilt-sunken text-gilt-soft font-mono text-[10px] uppercase tracking-[0.14em] border-b border-gilt-hairline">
            <tr>
              <th className="p-3 w-10">
                <input
                  type="checkbox"
                  checked={
                    filteredFiles.length > 0 &&
                    selectedFiles.length === filteredFiles.length
                  }
                  onChange={(e) => handleSelectAll(e, filteredFiles)}
                />
              </th>
              <th className="p-3">Name</th>
              <th className="p-3">Type</th>
              <th className="p-3">Size</th>
              <th className="p-3">Shared</th>
              <th className="p-3">Last modified</th>
              <th className="p-3 w-20">Actions</th>
            </tr>
          </thead>
          {/* Table Body */}
          <tbody className="text-sm text-gilt-soft">
            {filteredFiles.length > 0 ? (
              filteredFiles.map((file) => (
                <tr
                  key={file.file_id}
                  className="border-b border-gilt-hairline hover:bg-gilt-sunken transition-colors"
                >
                  {/* Checkbox */}
                  <td className="p-3">
                    <input
                      type="checkbox"
                      checked={selectedFiles.includes(file.file_id)}
                      onChange={() => handleSelectFile(file.file_id)}
                    />
                  </td>
                  {/* File Name + Icon */}
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <div className="relative">
                        {getFileIcon(file.file_type)}
                        {file.is_synced ? (
                          <div className="absolute -top-1 -right-1 w-2 h-2 bg-gilt-credit rounded-full border border-gilt-card"></div>
                        ) : (
                          <div className="absolute -top-1 -right-1 w-2 h-2 bg-gilt-brass rounded-full border border-gilt-card"></div>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="whitespace-nowrap text-gilt-ink">{file.file_name}</span>
                        <span className={`font-mono text-[10px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded-full border ${
                          file.is_synced
                            ? "text-gilt-credit border-gilt-credit/40 bg-transparent"
                            : "text-gilt-brass border-gilt-brass/40 bg-gilt-sunken"
                        }`}>
                          {file.is_synced ? "Synced" : "Not Synced"}
                        </span>
                      </div>
                    </div>
                  </td>
                  {/* Friendly File Type */}
                  <td className="p-3">{getFriendlyFileType(file.file_type)}</td>
                  {/* File Size */}
                  <td className="p-3 font-mono tabular-nums">
                    {file.size != null ? formatFileSize(file.size) : "—"}
                  </td>
                  {/* Shared with Icon + Label */}
                  <td className="p-3">{renderSharedValue(file.shared)}</td>
                  {/* Last Modified (elegant format) */}
                  <td className="p-3 whitespace-nowrap font-mono tabular-nums">
                    {formatDate(file.file_last_modified)}
                  </td>
                  {/* Actions Column */}
                  <td className="p-1">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => onFileClick && onFileClick(file)}
                        className="p-1 text-gilt-soft hover:text-gilt-brass transition-colors"
                        title="View file"
                      >
                        <FaEye className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => handleDeleteFile(file.file_id)}
                        className="p-1 text-gilt-soft hover:text-gilt-debit transition-colors"
                        title="Delete file"
                        disabled={isDeleting}
                      >
                        <FaTrash className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={7} className="p-4 text-center text-gilt-soft">
                  No files available.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
