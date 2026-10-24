// Human Voice Input: Web Speech API & Push-to-Talk

type SpeechCallback = (transcript: string, isFinal: boolean) => void;
type ErrorCallback = (error: string) => void;

interface IWindow extends Window {
  SpeechRecognition?: any;
  webkitSpeechRecognition?: any;
}

export class SpeechInputService {
  private recognition: any = null;
  private isListening: boolean = false;
  private onTranscript: SpeechCallback | null = null;
  private onError: ErrorCallback | null = null;

  constructor() {
    const win = window as IWindow;
    const SpeechRec = win.SpeechRecognition || win.webkitSpeechRecognition;
    if (SpeechRec) {
      try {
        this.recognition = new SpeechRec();
        this.recognition.continuous = false;
        this.recognition.interimResults = true;
        this.recognition.lang = "en-US";

        this.recognition.onresult = (event: any) => {
          let interim = "";
          let final = "";
          for (let i = event.resultIndex; i < event.results.length; ++i) {
            if (event.results[i].isFinal) {
              final += event.results[i][0].transcript;
            } else {
              interim += event.results[i][0].transcript;
            }
          }
          if (final && this.onTranscript) {
            this.onTranscript(final.trim(), true);
          } else if (interim && this.onTranscript) {
            this.onTranscript(interim.trim(), false);
          }
        };

        this.recognition.onerror = (event: any) => {
          this.isListening = false;
          if (this.onError) {
            this.onError(event.error || "Speech recognition error");
          }
        };

        this.recognition.onend = () => {
          this.isListening = false;
        };
      } catch (err) {
        console.warn("Speech recognition initialization failed:", err);
      }
    }
  }

  public isSupported(): boolean {
    return this.recognition !== null;
  }

  public getIsListening(): boolean {
    return this.isListening;
  }

  public start(onTranscript: SpeechCallback, onError?: ErrorCallback): boolean {
    if (!this.recognition) {
      if (onError) onError("Web Speech API not supported in this browser. Please type in the chat.");
      return false;
    }
    if (this.isListening) return true;

    this.onTranscript = onTranscript;
    this.onError = onError || null;
    try {
      this.isListening = true;
      this.recognition.start();
      return true;
    } catch (err) {
      this.isListening = false;
      if (onError) onError(String(err));
      return false;
    }
  }

  public stop() {
    if (this.recognition && this.isListening) {
      try {
        this.recognition.stop();
      } catch { /* ignore */ }
    }
    this.isListening = false;
  }
}

export const speechInput = new SpeechInputService();
