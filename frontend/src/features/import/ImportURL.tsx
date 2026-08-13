/**
 * This component is the URL-based entrypoint for cards into the project editor.
 * The backend returns a list of domains that it claims to know how to talk to,
 * displayed to the user. A freeform text box is exposed and the backend is asked
 * to process the URL when the user hits Submit.
 */

import React, { FormEvent, useCallback, useRef, useState } from "react";
import Button from "react-bootstrap/Button";
import Dropdown from "react-bootstrap/Dropdown";
import Form from "react-bootstrap/Form";
import Modal from "react-bootstrap/Modal";
import Stack from "react-bootstrap/Stack";

import {
  convertLinesIntoSlotProjectMembers,
  processStringAsMultipleLines,
} from "@/common/processing";
import { Face } from "@/common/schema_types";
import { useAppDispatch, useAppSelector } from "@/common/types";
import { RightPaddedIcon } from "@/components/icon";
import { Spinner } from "@/components/Spinner";
import { downloadFile } from "@/features/download/download";
import { useClientSearchContext } from "@/features/clientSearch/clientSearchContext";
import { buildOfficialImageSearchResults } from "@/features/import/officialImages";
import { useGetDFCPairsQuery, useGetImportSitesQuery } from "@/store/api";
import { api } from "@/store/api";
import { useProjectName } from "@/store/slices/backendSlice";
import { addCardDocuments } from "@/store/slices/cardDocumentsSlice";
import { addMembers, selectProjectSize } from "@/store/slices/projectSlice";
import { prependSearchResults } from "@/store/slices/searchResultsSlice";
import { selectFuzzySearch } from "@/store/slices/searchSettingsSlice";
import { setNotification } from "@/store/slices/toastsSlice";

interface ImportURLProps {
  onImportComplete?: () => void;
  inputRef?: React.RefObject<HTMLInputElement>;
}

export function ImportURL({ onImportComplete, inputRef }: ImportURLProps) {
  const dispatch = useAppDispatch();
  const dfcPairsQuery = useGetDFCPairsQuery();
  const importSitesQuery = useGetImportSitesQuery();
  const [triggerFn] = api.endpoints.queryImportSite.useLazyQuery();
  const projectName = useProjectName();
  const fuzzySearch = useAppSelector(selectFuzzySearch);
  const projectSize = useAppSelector(selectProjectSize);
  const { clientSearchService } = useClientSearchContext();

  const [urlValue, setUrlValue] = useState<string>("");
  const [downloadOfficialArt, setDownloadOfficialArt] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(false);
  const internalRef = useRef<HTMLInputElement>(null);
  const ref = inputRef ?? internalRef;

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmedURL = urlValue.trim();
      if (trimmedURL.length > 0) {
        setLoading(true);
        try {
          const query = await triggerFn(trimmedURL);
          const response = query.data;
          const decklistText = response?.cards ?? "";
          const officialImages = response?.officialImages ?? [];
          const processedLines = processStringAsMultipleLines(
            decklistText,
            dfcPairsQuery.data ?? {},
            fuzzySearch
          );

          if (officialImages.length > 0) {
            const { documents, searchResults } =
              buildOfficialImageSearchResults(officialImages);
            dispatch(addCardDocuments(documents));
            dispatch(prependSearchResults(searchResults));
          } else {
            dispatch(
              setNotification([
                "official-art-missing",
                {
                  name: "No Official Art Returned",
                  message:
                    "This backend did not return Scryfall PNG URLs for the deck. Point the editor at a backend that includes the official-art import feature.",
                  level: "warning",
                },
              ])
            );
          }

          dispatch(
            addMembers({
              members: convertLinesIntoSlotProjectMembers(
                processedLines,
                projectSize
              ),
            })
          );

          if (downloadOfficialArt && officialImages.length > 0) {
            const seen = new Set<string>();
            for (const image of officialImages) {
              if (image.face !== Face.Front) {
                continue;
              }
              if (seen.has(image.pngUrl)) {
                continue;
              }
              seen.add(image.pngUrl);
              const fileName = `${image.name}${
                image.expansionCode != null
                  ? ` (${image.expansionCode}) ${image.collectorNumber ?? ""}`
                  : ""
              }.png`.replace(/\s+/g, " ");
              try {
                await downloadFile(
                  undefined,
                  new URL(image.pngUrl),
                  fileName.trim(),
                  clientSearchService
                );
              } catch {
                // Keep importing even if an individual PNG download fails.
              }
            }
            dispatch(
              setNotification([
                "official-art-download",
                {
                  name: "Official Art Downloaded",
                  message: `Saved ${seen.size} Scryfall PNG(s) at highest quality.`,
                  level: "info",
                },
              ])
            );
          }

          setUrlValue("");
          onImportComplete?.();
        } catch (error: any) {
          dispatch(
            setNotification([
              "url-import-error",
              {
                name: "URL Import Error",
                message: `An unexpected error occurred while processing your decklist: ${error.message}`,
                level: "error",
              },
            ])
          );
        } finally {
          setLoading(false);
        }
      }
    },
    [
      dispatch,
      urlValue,
      dfcPairsQuery.data,
      projectSize,
      triggerFn,
      fuzzySearch,
      onImportComplete,
      downloadOfficialArt,
      clientSearchService,
    ]
  );

  const disabled =
    loading || importSitesQuery.isFetching || dfcPairsQuery.isFetching;

  if (
    !importSitesQuery.isFetching &&
    (importSitesQuery.data ?? []).length === 0
  ) {
    return null;
  }

  return (
    <>
      Paste a link to a card list hosted on one of the below sites (not
      affiliated) to import the list into {projectName}:
      <br />
      {importSitesQuery.data != null ? (
        <ul>
          {importSitesQuery.data.map((importSite) => (
            <li key={`${importSite.name}-row`}>
              <a key={importSite.name} href={importSite.url} target="_blank">
                {importSite.name}
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <>
          <br />
          <Spinner />
          <br />
        </>
      )}
      <Form onSubmit={handleSubmit}>
        <Form.Group className="mb-3">
          <Form.Control
            ref={ref}
            type="url"
            required={true}
            placeholder="https://"
            onChange={(event) => setUrlValue(event.target.value.trim())}
            value={urlValue}
            disabled={loading || importSitesQuery.data == null}
            aria-label="import-url"
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Check
            type="checkbox"
            id="download-official-art"
            label="Download official Scryfall art (highest quality PNG)"
            checked={downloadOfficialArt}
            onChange={(event) => setDownloadOfficialArt(event.target.checked)}
            disabled={loading}
          />
        </Form.Group>
        <Stack direction="horizontal" gap={1}>
          <div className="ms-auto">
            <Button
              type="submit"
              variant="primary"
              disabled={disabled}
              style={{ width: 4.75 + "em" }}
            >
              {loading ? <Spinner size={1.5} /> : "Submit"}
            </Button>
          </div>
        </Stack>
      </Form>
    </>
  );
}

export function ImportURLButton() {
  const importSitesQuery = useGetImportSitesQuery();
  const [show, setShow] = useState<boolean>(false);
  const focusRef = useRef<HTMLInputElement>(null);

  if (
    !importSitesQuery.isFetching &&
    (importSitesQuery.data ?? []).length === 0
  ) {
    return null;
  }

  return (
    <>
      <Dropdown.Item
        onClick={() => setShow(true)}
        disabled={importSitesQuery.isFetching}
      >
        {importSitesQuery.isFetching ? (
          <Spinner size={1.5} />
        ) : (
          <>
            <RightPaddedIcon bootstrapIconName="link-45deg" /> URL
          </>
        )}
      </Dropdown.Item>
      <Modal
        scrollable
        show={show}
        onEntered={() => focusRef.current?.focus()}
        onHide={() => setShow(false)}
      >
        <Modal.Header closeButton>
          <Modal.Title>Add Cards — URL</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <ImportURL
            onImportComplete={() => setShow(false)}
            inputRef={focusRef}
          />
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setShow(false)}>
            Close
          </Button>
        </Modal.Footer>
      </Modal>
    </>
  );
}
