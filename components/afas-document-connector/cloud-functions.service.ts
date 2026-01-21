import type { BackendComponentClient, ComponentContext } from "@ixon-cdk/types";
import {
  filesStore,
  selectedAgentStore,
  selectedAssetStore,
  loadingStore,
} from "./stores";

import type { PdfFile } from "./types";

export class CloudFunctionsService {
  context: ComponentContext;
  cloudFunctionsClient: BackendComponentClient;

  constructor(context: ComponentContext) {
    this.context = context;
    this.cloudFunctionsClient = context.createBackendComponentClient();
  }

  async init() {
    loadingStore.set(true);
    selectedAgentStore.set(await this.fetchResourceData(this.context, "Agent"));
    selectedAssetStore.set(await this.fetchResourceData(this.context, "Asset"));
    await this.getFiles();
  }

  async getFiles() {
    loadingStore.set(true);
    const response = await this.cloudFunctionsClient.call("get_files");

    if (response.data.error) {
      filesStore.set([]);
      loadingStore.set(false);
      return;
    }

    const files = response.data as any as { name: string; id: string }[];
    const processedFiles = files.map((file: { name: any; id: any }) => ({
      name: file.name,
      id: file.id,
      deleting: false,
      downloading: false,
    }));
    filesStore.set(processedFiles);
    loadingStore.set(false);
  }

  async downloadFile(file: PdfFile) {
    file.downloading = true;
    filesStore.update((files) => [...files]);
    const pdfFile = (
      await this.cloudFunctionsClient.call("download_file", {
        file_name: file.name,
        file_id: file.id,
      })
    ).data as any as { file_name: string; pdf_file: string };

    if (pdfFile.pdf_file && pdfFile.file_name) {
      // Assume pdfFile.pdf_file is a base64 data URI
      const response = await fetch(pdfFile.pdf_file);
      const blob = await response.blob();

      // Use the original filename from the file object to match what's shown in the UI
      // instead of the filename returned by AFAS which may be encoded differently
      this.context.saveAsFile(blob, file.name);
    } else {
      await this.getFiles();
    }

    file.downloading = false;
    filesStore.update((files) => [...files]);
  }

  private async fetchResourceData(
    context: ComponentContext,
    selector: string
  ): Promise<string | null> {
    const client = context.createResourceDataClient();
    const response = new Promise<string | null>((resolve) => {
      client.query(
        // @ts-ignore
        [{ selector, fields: ["name"] }],
        (result: { data: { name: any } }[]) => {
          resolve(result[0]?.data?.name || null);
          client.destroy();
        }
      );
    });

    return response;
  }
}
