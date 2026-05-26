/**
 * An audio worklet processor that stores the PCM audio data sent from the main thread
 * to a buffer and plays it.
 */
class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();

    // Init buffer
    this.bufferSize = 24000 * 180;  // 24kHz x 180 seconds
    this.buffer = new Float32Array(this.bufferSize);
    this.writeIndex = 0;
    this.readIndex = 0;

    // NEW: Flag to track when we are waiting for the final audio to finish
    this.isFlushing = false;    

    // Handle incoming messages from main thread
    this.port.onmessage = (event) => {
      // Reset the buffer when 'endOfAudio' message received
      if (event.data.command === 'endOfAudio') {
        this.readIndex = this.writeIndex; // Clear the buffer
        this.isFlushing = false; // Reset the flag for the next turn
        console.log("endOfAudio received, clearing the buffer.");
        return;
      }

      // Listen for the 'endOfTurn' signal from app.js that indicates all the audio chunks are now in buffer
      if (event.data.command === 'endOfTurn') {
        // TRACER 2: Did the Worklet receive the command?
        console.log("2. WORKLET: endOfTurn received. isFlushing armed.");        
        this.isFlushing = true;
        return;
      }

      // Decode the base64 data to int16 array.
      const int16Samples = new Int16Array(event.data);

      // Add the audio data to the buffer
      this._enqueue(int16Samples);
    };
  }

  // Push incoming Int16 data into our ring buffer.
  _enqueue(int16Samples) {
    for (let i = 0; i < int16Samples.length; i++) {
      // Convert 16-bit integer to float in [-1, 1]
      const floatVal = int16Samples[i] / 32768;

      // Store in ring buffer for left channel only (mono)
      this.buffer[this.writeIndex] = floatVal;
      this.writeIndex = (this.writeIndex + 1) % this.bufferSize;

      // Overflow handling (overwrite oldest samples)
      if (this.writeIndex === this.readIndex) {
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
      }
    }
  }

  // The system calls `process()` ~128 samples at a time (depending on the browser).
  // We fill the output buffers from our ring buffer.
  process(inputs, outputs, parameters) {

    // Write a frame to the output
    const output = outputs[0];
    const framesPerBlock = output[0].length;
    for (let frame = 0; frame < framesPerBlock; frame++) {

      // Write the sample(s) into the output buffer
      output[0][frame] = this.buffer[this.readIndex]; // left channel
      if (output.length > 1) {
        output[1][frame] = this.buffer[this.readIndex]; // right channel
      }

      // Move the read index forward unless underflowing
      if (this.readIndex != this.writeIndex) {
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
      }
    }

    // If we are flushing AND the buffer is officially empty, tell the main thread!
    if (this.isFlushing && this.readIndex === this.writeIndex) {
      // TRACER 3: Did the buffer actually empty out?
      console.log("3. WORKLET: Buffer empty! Firing playbackComplete flare.");      
      this.port.postMessage({ command: 'playbackComplete' });
      this.isFlushing = false; // Reset the flag for the next turn
    }

    // Returning true tells the system to keep the processor alive
    return true;
  }
}

registerProcessor('pcm-player-processor', PCMPlayerProcessor);
