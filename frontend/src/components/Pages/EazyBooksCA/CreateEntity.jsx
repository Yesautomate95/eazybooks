import { useState, useContext } from "react";
import { useNavigate } from "react-router-dom";
import { CloudUpload, X, AlertCircle } from "lucide-react";
import { getToken } from "../../../services/auth";
import { EazyBooksContext } from "../../context/EazyBooksContext";
import { clientsApi, filesApi } from "../../../services/api";
import api from "../../../services/api";

export default function CreateEntity({
  onBack,
  entityLabel,     // e.g., "Locker" or "Client"
  createEndpoint,  // Now using environment variable from parent components
  entityIdKey,     // e.g., "lockr_id" or "client_id"
  description,     // Optional description text
}) {
  const navigate = useNavigate();
  const { user, isAuthenticated, setIsAuthenticated } = useContext(EazyBooksContext);
  const [entityName, setEntityName] = useState("");
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Tab state for switching between "Suggested Files" and "Drag & Drop"
  const [activeTab, setActiveTab] = useState("drag");

  // State for suggested files and file preview modal
  const [suggestedFiles, setSuggestedFiles] = useState([]);
  const [viewedFile, setViewedFile] = useState(null);

  const supportedFormats = ["pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tiff", "ico"];

  const validateFile = (file) => {
    const extension = file.name.split(".").pop().toLowerCase();
    return supportedFormats.includes(extension);
  };

  const handleFileChange = (e) => {
    const newFiles = Array.from(e.target.files);
    const validFiles = newFiles.filter(validateFile);
    const invalidFiles = newFiles.filter((file) => !validateFile(file));

    if (invalidFiles.length > 0) {
      alert(
        `Some files were not added because they are not in supported formats. Supported formats are: ${supportedFormats.join(
          ", "
        ).toUpperCase()}`
      );
    }
    setFiles((prevFiles) => [...prevFiles, ...validFiles]);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const newFiles = Array.from(e.dataTransfer.files);
    const validFiles = newFiles.filter(validateFile);
    const invalidFiles = newFiles.filter((file) => !validateFile(file));

    if (invalidFiles.length > 0) {
      alert(
        `Some files were not added because they are not in supported formats. Supported formats are: ${supportedFormats.join(
          ", "
        ).toUpperCase()}`
      );
    }
    setFiles((prevFiles) => [...prevFiles, ...validFiles]);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
  };

  const validateAuth = async () => {
    try {
      const token = getToken();
      
      if (!token) {
        setIsAuthenticated(false);
        return false;
      }
      return true;
    } catch (err) {
      console.error("Error validating auth:", err);
      setIsAuthenticated(false);
      return false;
    }
  };

  const handleSubmit = async () => {
    setError(null);
    
    const isValid = await validateAuth();
    if (!isValid) {
      setError("Your session has expired. Please sign in again.");
      return;
    }

    if (!entityName || files.length === 0) {
      alert(
        `Please enter a ${entityLabel.toLowerCase()} name and upload at least one file.`
      );
      return;
    }

    setLoading(true);

    try {
      // Get Firebase ID token
      const token = getToken();
      if (!token) {
        throw new Error('Authentication token not found. Please sign in again.');
      }

      // 1. Create the entity (locker/client)
      try {
        const createFn = entityIdKey === 'lockr_id' ? clientsApi.createLocker : clientsApi.createClient;
        const createData = await createFn({ name: entityName });

        if (!createData || !createData[entityIdKey]) {
          throw new Error('Invalid response from server. Please try again.');
        }

        const entity_id = createData[entityIdKey];

        // 2. Upload files using the /upload endpoint
        const formData = new FormData();
        formData.append(entityIdKey, entity_id);
        files.forEach((file) => {
          formData.append("files", file);
        });

        await filesApi.upload(formData);

        localStorage.removeItem(`lockers`);
        localStorage.removeItem(`lockers_timestamp`);
        localStorage.removeItem(`clients`);
        localStorage.removeItem(`clients_timestamp`);
        // Add a small delay to ensure the backend has processed everything
        await new Promise((resolve) => setTimeout(resolve, 1000));
        onBack();
      } catch (err) {
        if (err.response?.status === 404) {
          throw new Error("Your account is not properly initialized. Please try logging out and back in.");
        } else if (err.response?.status === 401) {
          setIsAuthenticated(false);
          throw new Error("Your session has expired. Please sign in again.");
        } else if (err.response?.data?.detail) {
          throw new Error(err.response.data.detail);
        } else {
          throw err;
        }
      }
    } catch (err) {
      console.error("Error creating entity:", err);
      setError(err.message || "An error occurred while creating the entity.");
    } finally {
      setLoading(false);
    }
  };

  const handleGetSuggestedFiles = async () => {
    if (!entityName) {
      alert(
        `Please enter a ${entityLabel.toLowerCase()} name before fetching suggested files.`
      );
      return;
    }
    try {
      setLoading(true);
      const dirHandle = await window.showDirectoryPicker();
      const localFiles = [];
      for await (const entry of dirHandle.values()) {
        if (entry.kind === "file") {
          const file = await entry.getFile();
          localFiles.push(file);
        }
      }

      const formData = new FormData();
      formData.append("search_context", entityName);
      localFiles.forEach((file) => {
        formData.append("files", file);
      });

      const response = await api.post('/ca/search-suggested-files', formData);

      const matchingFileNames = response.data.matching_files;
      const matchedFiles = localFiles.filter((file) =>
        matchingFileNames.includes(file.name)
      );
      setSuggestedFiles(matchedFiles);
    } catch (error) {
      console.error("Error fetching suggested files:", error);
      alert("Failed to fetch suggested files. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const addSuggestedFile = (file) => {
    if (!files.some((f) => f.name === file.name)) {
      setFiles((prev) => [...prev, file]);
    }
  };

  const handleViewFile = (file) => {
    setViewedFile(file);
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-2xl bg-gilt-card rounded-lg p-8 border border-gilt-hairline">
        <h1 className="gl-display text-3xl text-gilt-ink mb-2">
          Create New {entityLabel}
        </h1>
        <p className="text-sm text-gilt-soft mb-6">
          {description ||
            `Manage your files with a new ${entityLabel.toLowerCase()}`}
        </p>

        {error && (
          <div className="mb-6 p-4 bg-gilt-sunken border border-gilt-debit/40 rounded-lg flex items-start space-x-2">
            <AlertCircle className="h-5 w-5 text-gilt-debit flex-shrink-0 mt-0.5" />
            <p className="text-gilt-debit text-sm">{error}</p>
          </div>
        )}

        <div className="mb-6">
          <label className="block font-mono text-[10px] uppercase tracking-[0.14em] text-gilt-soft mb-2">
            {entityLabel} Name
          </label>
          <input
            type="text"
            value={entityName}
            onChange={(e) => setEntityName(e.target.value)}
            className="w-full p-3 bg-gilt-paper border border-gilt-hairline rounded-lg focus:outline-none focus:border-gilt-brass text-gilt-ink placeholder:text-gilt-faint transition-colors"
            placeholder={`Enter ${entityLabel.toLowerCase()} name`}
          />
        </div>

        {/* Tabs for switching between Suggested Files and Drag & Drop */}
        <div className="flex items-center justify-center mb-6">
          <div className="flex bg-gilt-sunken border border-gilt-hairline rounded-full p-1 w-max">
            <button
              onClick={() => setActiveTab("suggested")}
              className={`px-4 py-1 text-sm font-medium rounded-full transition-colors duration-200 focus:outline-none ${
                activeTab === "suggested"
                  ? "bg-gilt-card text-gilt-ink"
                  : "text-gilt-soft hover:text-gilt-ink"
              }`}
            >
              Suggested Files
            </button>
            <button
              onClick={() => setActiveTab("drag")}
              className={`px-4 py-1 text-sm font-medium rounded-full transition-colors duration-200 focus:outline-none ${
                activeTab === "drag"
                  ? "bg-gilt-card text-gilt-ink"
                  : "text-gilt-soft hover:text-gilt-ink"
              }`}
            >
              Drag & Drop
            </button>
          </div>
        </div>

        {/* Suggested Files Tab */}
        {activeTab === "suggested" && (
          <div className="mb-6">
            <div
              className="flex flex-col border-2 border-dashed border-gilt-hairline rounded-xl p-12 text-center bg-gilt-sunken/50 transition-colors duration-200 group min-h-[320px]"
            >
              {suggestedFiles.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full">
                  <button
                    onClick={handleGetSuggestedFiles}
                    className="inline-block px-4 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:opacity-90 transition-opacity"
                  >
                    Get Suggested Files
                  </button>
                </div>
              ) : (
                <div className="max-h-48 w-full overflow-y-auto">
                  <ul className="space-y-2">
                    {suggestedFiles.map((file, index) => (
                      <li
                        key={index}
                        className="flex items-center justify-between text-sm bg-gilt-card p-3 rounded-lg border border-gilt-hairline"
                      >
                        <div className="flex items-center space-x-3">
                          <span className="text-xs font-mono text-gilt-faint">
                            {file.name.split(".").pop().toUpperCase()}
                          </span>
                          <span className="truncate text-gilt-ink">
                            {file.name}
                          </span>
                        </div>
                        <div className="flex space-x-2">
                          <button
                            onClick={() => handleViewFile(file)}
                            className="px-2 py-1 text-xs border border-gilt-hairline text-gilt-soft rounded hover:border-gilt-brass hover:text-gilt-brass transition-colors"
                          >
                            View
                          </button>
                          {files.some((f) => f.name === file.name) ? (
                            <button className="px-2 py-1 text-xs bg-gilt-sunken text-gilt-faint border border-gilt-hairline rounded cursor-not-allowed">
                              Added
                            </button>
                          ) : (
                            <button
                              onClick={() => addSuggestedFile(file)}
                              className="px-2 py-1 text-xs bg-gilt-brass text-gilt-paper rounded hover:opacity-90 transition-opacity"
                            >
                              Add
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Selected Files List */}
            {files.length > 0 && (
              <div className="mt-8 w-full">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-sm font-medium text-gilt-soft">
                    Selected files (<span className="font-mono tabular-nums">{files.length}</span>)
                  </p>
                  <button
                    onClick={() => setFiles([])}
                    className="text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                  >
                    Clear all
                  </button>
                </div>
                <ul className="space-y-2 max-h-48 overflow-y-auto">
                  {files.map((file, index) => (
                    <li
                      key={index}
                      className="flex items-center justify-between text-sm bg-gilt-card p-3 rounded-lg border border-gilt-hairline hover:border-gilt-brass transition-colors"
                    >
                      <div className="flex items-center space-x-3">
                        <div className="flex-shrink-0 w-8 h-8 bg-gilt-sunken border border-gilt-hairline rounded-lg flex items-center justify-center">
                          <span className="text-xs font-mono text-gilt-brass">
                            {file.name.split(".").pop().toUpperCase()}
                          </span>
                        </div>
                        <span className="truncate text-gilt-ink">
                          {file.name}
                        </span>
                      </div>
                      <button
                        onClick={() =>
                          setFiles((prev) =>
                            prev.filter((_, i) => i !== index)
                          )
                        }
                        className="p-1 hover:bg-gilt-sunken rounded-full transition-colors flex-shrink-0"
                      >
                        <X className="h-4 w-4 text-gilt-soft" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Drag & Drop Tab */}
        {activeTab === "drag" && (
          <div className="mb-6">
            <div
              className="flex flex-col items-center justify-center border-2 border-dashed border-gilt-hairline rounded-xl p-12 text-center bg-gilt-sunken/50 cursor-pointer hover:bg-gilt-sunken transition-colors duration-200 hover:border-gilt-brass group min-h-[320px]"
              onDrop={handleDrop}
              onDragOver={handleDragOver}
            >
              <div className="bg-gilt-sunken border border-gilt-hairline p-4 rounded-full mb-6 transition-colors duration-200">
                <CloudUpload className="h-12 w-12 text-gilt-brass" />
              </div>
              <div className="space-y-2">
                <p className="text-lg font-medium text-gilt-soft">
                  Drag & drop files here
                </p>
                <p className="text-sm text-gilt-soft">or</p>
                <input
                  type="file"
                  multiple
                  accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg,.tiff,.ico"
                  onChange={handleFileChange}
                  className="hidden"
                  id="file-upload"
                />
                <label
                  htmlFor="file-upload"
                  className="inline-block px-4 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:opacity-90 transition-opacity cursor-pointer"
                >
                  Browse Files
                </label>
                <p className="text-xs text-gilt-faint mt-2">
                  Supported formats: PDF, DOC, DOCX, XLS, XLSX, PNG, JPG, JPEG, GIF, BMP, WEBP, SVG, TIFF, ICO
                </p>
              </div>
            </div>

            {/* Selected Files List */}
            {files.length > 0 && (
              <div className="mt-8 w-full">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-sm font-medium text-gilt-soft">
                    Selected files (<span className="font-mono tabular-nums">{files.length}</span>)
                  </p>
                  <button
                    onClick={() => setFiles([])}
                    className="text-sm text-gilt-soft hover:text-gilt-ink transition-colors"
                  >
                    Clear all
                  </button>
                </div>
                <ul className="space-y-2 max-h-48 overflow-y-auto">
                  {files.map((file, index) => (
                    <li
                      key={index}
                      className="flex items-center justify-between text-sm bg-gilt-card p-3 rounded-lg border border-gilt-hairline hover:border-gilt-brass transition-colors"
                    >
                      <div className="flex items-center space-x-3">
                        <div className="flex-shrink-0 w-8 h-8 bg-gilt-sunken border border-gilt-hairline rounded-lg flex items-center justify-center">
                          <span className="text-xs font-mono text-gilt-brass">
                            {file.name.split(".").pop().toUpperCase()}
                          </span>
                        </div>
                        <span className="truncate text-gilt-ink">
                          {file.name}
                        </span>
                      </div>
                      <button
                        onClick={() =>
                          setFiles((prev) =>
                            prev.filter((_, i) => i !== index)
                          )
                        }
                        className="p-1 hover:bg-gilt-sunken rounded-full transition-colors flex-shrink-0"
                      >
                        <X className="h-4 w-4 text-gilt-soft" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex justify-end space-x-4 mt-8">
          <button
            onClick={onBack}
            className="px-6 py-2 text-sm font-medium text-gilt-soft bg-transparent border border-gilt-hairline hover:border-gilt-brass hover:text-gilt-brass rounded-md transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="px-6 py-2 text-sm font-medium text-gilt-paper bg-gilt-brass rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {loading ? `Creating...` : `Create ${entityLabel}`}
          </button>
        </div>

        {/* Error Message */}
        {error && (
          <div className="mt-6 text-center text-sm text-gilt-debit">
            Error: {error}
          </div>
        )}
      </div>

      {/* File Preview Modal */}
      {viewedFile && (
        <div className="fixed inset-0 bg-gilt-ink/40 flex items-center justify-center z-50">
          <div className="bg-gilt-card border border-gilt-hairline p-4 md:p-6 rounded-lg relative max-w-4xl w-full h-[80vh] flex flex-col">
            <button
              onClick={() => setViewedFile(null)}
              className="absolute top-2 right-2 text-gilt-soft hover:text-gilt-ink transition-colors"
            >
              <X className="h-6 w-6" />
            </button>
            <h2 className="text-lg font-medium text-gilt-ink mb-4">{viewedFile.name}</h2>
            {viewedFile.name.toLowerCase().endsWith(".pdf") ? (
              <iframe
                src={URL.createObjectURL(viewedFile)}
                className="flex-1"
                title={viewedFile.name}
              ></iframe>
            ) : (
              <div className="flex-1 p-4 overflow-auto">
                <p>Preview not available for this file type.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
