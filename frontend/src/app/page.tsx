// frontend/app/page.tsx
"use client";

import { useState } from "react";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [uploadedFilename, setUploadedFilename] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string>("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string>("");
  const [loadingUpload, setLoadingUpload] = useState(false);
  const [loadingAsk, setLoadingAsk] = useState(false);

  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) {
      toast.error("File too large. Max 10 MB.");
      setFile(null);
      return;
    }
    setFile(f);
  };

  const handleUpload = async () => {
    if (!file) {
      toast.error("Please select a PDF first.");
      return;
    }
    try {
      setLoadingUpload(true);
      setUploadStatus("Uploading...");
      const fd = new FormData();
      fd.append("file", file);

      const res = await axios.post(`${backendUrl}/upload-pdf`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      if (res.data?.status === "error") {
        toast.error(res.data.message || "Upload failed");
        setUploadStatus("Upload failed");
        return;
      }

      const { filename, chunks_stored } = res.data;
      setUploadedFilename(filename ?? null);
      setUploadStatus(`Uploaded: ${filename} (${chunks_stored} chunks stored)`);
      toast.success(`Upload successful: ${filename}`);
    } catch (err) {
      console.error(err);
      setUploadStatus("Upload failed");
      toast.error("Upload failed. See console for details.");
    } finally {
      setLoadingUpload(false);
    }
  };

  const handleAsk = async () => {
    if (!question.trim()) {
      toast.error("Please enter a question.");
      return;
    }
    if (!uploadedFilename) {
      toast.error("No uploaded PDF. Please upload a PDF first.");
      return;
    }

    try {
      setLoadingAsk(true);
      setAnswer("Thinking...");
      const res = await axios.post(`${backendUrl}/ask`, {
        question,
        filename: uploadedFilename,
      });

      if (res.data?.status === "error") {
        toast.error(res.data.message || "Error getting answer");
        setAnswer("");
        return;
      }

      setAnswer(res.data.answer ?? "No answer returned.");
      toast.success("Answer received");
    } catch (err) {
      console.error(err);
      setAnswer("Failed to get answer.");
      toast.error("Failed to get answer. See console.");
    } finally {
      setLoadingAsk(false);
    }
  };

  return (
    <main className="flex flex-col items-center min-h-screen bg-gray-50 p-6">
      <h1 className="text-3xl font-bold mb-6">📄 PDF Q&A App</h1>

      <Card className="w-full max-w-xl mb-6">
        <CardHeader>
          <CardTitle>Upload PDF</CardTitle>
        </CardHeader>
        <CardContent>
          <Input type="file" accept="application/pdf" onChange={handleFileChange} />
          <div className="flex gap-2 mt-3">
            <Button onClick={handleUpload} disabled={loadingUpload}>
              {loadingUpload ? "Uploading..." : "Upload"}
            </Button>
            <div className="self-center text-sm text-muted-foreground">
              {file ? file.name : "No file selected"}
            </div>
          </div>
          <p className="text-sm mt-2">{uploadStatus}</p>
          {uploadedFilename && <p className="text-sm mt-1">Active file: {uploadedFilename}</p>}
        </CardContent>
      </Card>

      <Card className="w-full max-w-xl">
        <CardHeader>
          <CardTitle>Ask a Question</CardTitle>
        </CardHeader>
        <CardContent>
          <Textarea
            placeholder="Type your question..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            className="mb-2"
          />
          <div className="flex gap-2">
            <Button onClick={handleAsk} disabled={loadingAsk}>
              {loadingAsk ? "Asking..." : "Ask"}
            </Button>
            <div className="self-center text-sm text-muted-foreground">
              {uploadedFilename ? `Using: ${uploadedFilename}` : "No PDF selected"}
            </div>
          </div>

          {answer && (
            <div className="mt-4 p-3 border rounded bg-white">
              <strong>Answer:</strong>
              <p className="mt-2 whitespace-pre-line">{answer}</p>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
