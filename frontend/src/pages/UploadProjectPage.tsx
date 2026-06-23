import { FileArchive, FileUp, FolderOpen, Upload } from "lucide-react";
import { ChangeEvent, useRef, useState } from "react";
import { uploadProject } from "../api/client";
import type { UploadResponse } from "../types/api";

interface Props {
  onUploaded: (response: UploadResponse) => void;
}

export default function UploadProjectPage({ onUploaded }: Props) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setError("");
    setSelectedFiles(Array.from(event.target.files ?? []));
  };

  const submit = async () => {
    if (!selectedFiles.length) {
      setError("Choose a ZIP file, CSV files, or a scenario folder.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await uploadProject(selectedFiles);
      onUploaded(response);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Project setup</p>
          <h1>Upload Scenario</h1>
        </div>
        <button className="primary-button" onClick={submit} disabled={busy || !selectedFiles.length}>
          <Upload size={18} aria-hidden="true" />
          {busy ? "Uploading" : "Upload"}
        </button>
      </header>

      <div className="upload-grid">
        <label className="upload-target">
          <FileArchive size={28} aria-hidden="true" />
          <strong>ZIP</strong>
          <span>One archive containing the scenario CSV files.</span>
          <input type="file" accept=".zip" onChange={handleFiles} />
        </label>
        <label className="upload-target">
          <FileUp size={28} aria-hidden="true" />
          <strong>CSV files</strong>
          <span>Select the required CSV files together.</span>
          <input type="file" accept=".csv" multiple onChange={handleFiles} />
        </label>
        <label className="upload-target">
          <FolderOpen size={28} aria-hidden="true" />
          <strong>Folder</strong>
          <span>Use a browser that supports folder selection.</span>
          <input
            ref={folderInputRef}
            type="file"
            multiple
            onChange={handleFiles}
            {...({ webkitdirectory: "true", directory: "true" } as Record<string, string>)}
          />
        </label>
      </div>

      <div className="panel">
        <h2>Selected Files</h2>
        {selectedFiles.length ? (
          <div className="file-list">
            {selectedFiles.slice(0, 14).map((file) => (
              <span key={`${file.name}-${file.size}`}>{file.webkitRelativePath || file.name}</span>
            ))}
            {selectedFiles.length > 14 ? <span>+{selectedFiles.length - 14} more</span> : null}
          </div>
        ) : (
          <p className="muted">No files selected.</p>
        )}
        {error ? <p className="error-text">{error}</p> : null}
      </div>
    </section>
  );
}
