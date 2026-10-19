const roomId = document.body.dataset.roomId;

if (roomId) {
  const fileInput = document.getElementById("file-input");
  const dropZone = document.getElementById("drop-zone");
  const fileList = document.getElementById("file-list");
  const fileCount = document.getElementById("file-count");
  const uploadStatus = document.getElementById("upload-status");
  const shareUrl = document.getElementById("share-url");
  const roomUrl = window.location.href;
  const expiryClock = document.getElementById("expiry-clock");
  const expiresAt = Number(document.body.dataset.expiresAt) * 1000;

  shareUrl.value = roomUrl;

  const formatBytes = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024)
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  const updateExpiry = () => {
    const remaining = Math.max(0, expiresAt - Date.now());
    const hours = Math.floor(remaining / 3600000);
    const minutes = Math.floor((remaining % 3600000) / 60000);
    expiryClock.textContent =
      remaining === 0
        ? "Expired"
        : hours
          ? `${hours}h ${minutes}m`
          : `${minutes} minutes`;
  };

  const setUploadStatus = (message, isError = false) => {
    uploadStatus.textContent = message;
    uploadStatus.classList.toggle("is-error", isError);
  };

  const showUploadProgress = (loaded, total) => {
    const percentage = total
      ? Math.min(100, Math.round((loaded / total) * 100))
      : 0;
    const label = document.createElement("span");
    label.textContent = `Uploading ${formatBytes(loaded)} of ${formatBytes(total)} · ${percentage}%`;
    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = percentage;
    progress.setAttribute("aria-label", "Upload progress");
    uploadStatus.replaceChildren(label, progress);
    uploadStatus.classList.remove("is-error");
  };

  const refreshFiles = async () => {
    try {
      const response = await fetch(`/r/${encodeURIComponent(roomId)}/files`);
      if (!response.ok) return;
      const files = await response.json();
      fileCount.textContent = files.length;
      fileList.replaceChildren();
      if (!files.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        const mark = document.createElement("span");
        mark.className = "empty-mark";
        mark.textContent = "↗";
        const label = document.createElement("p");
        label.textContent = "Nothing here yet. Add a file to get started.";
        empty.append(mark, label);
        fileList.append(empty);
        return;
      }

      for (const file of files) {
        const row = document.createElement("article");
        row.className = "file-row";
        const main = document.createElement("div");
        main.className = "file-main";
        const icon = document.createElement("span");
        icon.className = "file-icon";
        icon.textContent = (
          file.original_name.split(".").pop() || "file"
        ).slice(0, 4);
        const name = document.createElement("span");
        name.className = "file-name";
        name.textContent = file.original_name;
        main.append(icon, name);
        const meta = document.createElement("span");
        meta.className = "file-meta";
        meta.textContent = formatBytes(file.size);
        const link = document.createElement("a");
        link.className = "download-link";
        link.href = `/r/${encodeURIComponent(roomId)}/download/${encodeURIComponent(file.id)}`;
        link.download = file.original_name;
        link.textContent = "↓";
        link.setAttribute("aria-label", `Download ${file.original_name}`);
        const actions = document.createElement("div");
        actions.className = "file-actions";
        actions.append(link);
        const deleteButton = document.createElement("button");
        deleteButton.className = "delete-button";
        deleteButton.type = "button";
        deleteButton.textContent = "×";
        deleteButton.title = "Delete file";
        deleteButton.setAttribute("aria-label", `Delete ${file.original_name}`);
        deleteButton.addEventListener("click", async () => {
          if (!window.confirm(`Delete “${file.original_name}” from this room?`))
            return;
          deleteButton.disabled = true;
          setUploadStatus(`Deleting ${file.original_name}…`);
          try {
            const response = await fetch(
              `/r/${encodeURIComponent(roomId)}/files/${encodeURIComponent(file.id)}`,
              { method: "DELETE" },
            );
            if (!response.ok)
              throw new Error("Could not delete this file. Try again.");
            setUploadStatus("File deleted.");
            await refreshFiles();
          } catch (error) {
            setUploadStatus(error.message || "Delete failed. Try again.", true);
            deleteButton.disabled = false;
          }
        });
        actions.append(deleteButton);
        row.append(main, meta, actions);
        fileList.append(row);
      }
    } catch {
      setUploadStatus("Connection interrupted. Reconnecting…", true);
    }
  };

  const upload = async (files) => {
    if (!files.length) return;
    if (files.length > 30) {
      setUploadStatus("Choose up to 30 files at a time.", true);
      return;
    }
    const totalSize = files.reduce((total, file) => total + file.size, 0);
    if (totalSize > Number(document.body.dataset.maxUploadMb) * 1024 * 1024) {
      setUploadStatus(
        `These files exceed the ${document.body.dataset.maxUploadMb} MB transfer limit.`,
        true,
      );
      return;
    }

    const formData = new FormData();
    for (const file of files) formData.append("files", file);
    setUploadStatus(
      `Preparing ${files.length} ${files.length === 1 ? "file" : "files"}…`,
    );
    try {
      const result = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `/r/${encodeURIComponent(roomId)}/upload`);
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable)
            showUploadProgress(event.loaded, event.total);
        });
        xhr.addEventListener("load", () => {
          let response;
          try {
            response = JSON.parse(xhr.responseText);
          } catch {
            reject(new Error("The server returned an unexpected response."));
            return;
          }
          if (xhr.status < 200 || xhr.status >= 300) {
            reject(new Error(response.error || "Upload failed."));
            return;
          }
          resolve(response);
        });
        xhr.addEventListener("error", () =>
          reject(
            new Error("Upload failed. Check your connection and try again."),
          ),
        );
        xhr.send(formData);
      });
      setUploadStatus(
        `${result.uploaded} ${result.uploaded === 1 ? "file" : "files"} ready to pick up.`,
      );
      await refreshFiles();
    } catch (error) {
      setUploadStatus(error.message || "Upload failed. Try again.", true);
    } finally {
      fileInput.value = "";
    }
  };

  document
    .getElementById("browse-button")
    .addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => upload([...fileInput.files]));
  dropZone.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    fileInput.click();
  });
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-over");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-over");
    });
  }
  dropZone.addEventListener("drop", (event) =>
    upload([...event.dataTransfer.files]),
  );
  document
    .getElementById("copy-link")
    .addEventListener("click", async (event) => {
      try {
        await navigator.clipboard.writeText(roomUrl);
        event.currentTarget.textContent = "Copied";
        window.setTimeout(() => {
          event.currentTarget.textContent = "Copy link";
        }, 1800);
      } catch {
        shareUrl.select();
        document.execCommand("copy");
      }
    });

  updateExpiry();
  refreshFiles();
  window.setInterval(updateExpiry, 60000);
  window.setInterval(refreshFiles, 5000);
}
