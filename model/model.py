import torch
import torchaudio
import torchaudio.transforms as T
from huggingface_hub import hf_hub_download
from pathlib import Path
import torch.nn as nn
from threading import Thread, Event
import numpy as np


# credits: https://huggingface.co/AntoineNaccache/drone-audio-detector

# load weights
ckpt_path = Path("drone_classifier_aug_mixed.pt")
test_audio_path = Path("drone_recordings/Binary_Drone_Audio/yes_drone/B_S2_D1_067-bebop_000_.wav")

if not ckpt_path.is_file():
    raise FileNotFoundError(f"Model weights not found: {ckpt_path}")


def _conv_block(in_ch, out_ch):
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False), nn.BatchNorm2d(out_ch), nn.ReLU(True),
        nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False), nn.BatchNorm2d(out_ch), nn.ReLU(True),
    )

class DroneClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc1  = _conv_block(1, 32);   self.pool1 = nn.MaxPool2d(2, 2)
        self.enc2  = _conv_block(32, 64);  self.pool2 = nn.MaxPool2d(2, 2)
        self.enc3  = _conv_block(64, 128); self.pool3 = nn.MaxPool2d((2, 1), (2, 1))
        self.gru   = nn.GRU(1024, 128, num_layers=2, batch_first=True,
                            bidirectional=True, dropout=0.2)
        self.head  = nn.Sequential(nn.Linear(256, 64), nn.ReLU(True),
                                   nn.Dropout(0.3), nn.Linear(64, 1))
        self.mel_transform = T.MelSpectrogram(
            sample_rate=16_000, n_fft=512, hop_length=160,
            n_mels=64, f_min=50, f_max=5_500,
        )
        self.state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        self.load_state()

    def load_state(self):
        state = self.state
        if "model_state_dict" in state:
            state = state["model_state_dict"]
        self.load_state_dict(state, strict=False)
        self.eval()

    def forward(self, x):
        x = self.pool1(self.enc1(x))
        x = self.pool2(self.enc2(x))
        x = self.pool3(self.enc3(x))
        B, C, F, T = x.shape
        x, _ = self.gru(x.permute(0, 3, 1, 2).reshape(B, T, C * F))
        return self.head(x.mean(1))

    def predict(self, samples : np.ndarray) -> float:
        arr = np.asarray(samples, dtype=np.float32).ravel()[:16_000] / 32768.0
        waveform = torch.from_numpy(arr).unsqueeze(0)  # (1, N)
        waveform = waveform.mean(0, keepdim=True)[:, :16_000]   # mono, 1 s
        log_mel = (T.AmplitudeToDB()(self.mel_transform(waveform)) + 40) / 40  # ≈ [-1, 1]
        x = log_mel.unsqueeze(0)   # (1, 1, 64, T)
        with torch.no_grad():
            prob = torch.sigmoid(self(x)).item()
        print(f"prediction: {prob:/3f}")
        return prob
