package com.google.gson.stream;

import com.google.gson.internal.JsonReaderInternalAccess;
import org.junit.Test;

import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;

import static org.junit.Assert.*;
import static org.junit.Assert.assertEquals;

public class JsonReader_nextLong_Test {

  @Test
  public void testNextLong_NormalLongValue() throws IOException {
    Reader reader = new StringReader("1234567890");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(1234567890L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_NegativeLongValue() throws IOException {
    Reader reader = new StringReader("-9876543210");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(-9876543210L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_Zero() throws IOException {
    Reader reader = new StringReader("0");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(0L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_MaxLong() throws IOException {
    Reader reader = new StringReader("9223372036854775807");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(Long.MAX_VALUE, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_MinLong() throws IOException {
    Reader reader = new StringReader("-9223372036854775808");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(Long.MIN_VALUE, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_WithWhitespace() throws IOException {
    Reader reader = new StringReader("  42  ");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(42L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_FromDoubleString() throws IOException {
    Reader reader = new StringReader("123.0");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(123L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_FromScientificNotation() throws IOException {
    Reader reader = new StringReader("1.23E2");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(123L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_FromQuotedString() throws IOException {
    Reader reader = new StringReader("\"9876543210\"");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(9876543210L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test
  public void testNextLong_FromUnquotedString() throws IOException {
    Reader reader = new StringReader("4567890123");
    JsonReader jsonReader = new JsonReader(reader);
    assertEquals(4567890123L, jsonReader.nextLong());
    jsonReader.close();
  }

  @Test(expected = NumberFormatException.class)
  public void testNextLong_InvalidNumberFormat() throws IOException {
    Reader reader = new StringReader("123abc");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.nextLong();
    jsonReader.close();
  }

  @Test(expected = NumberFormatException.class)
  public void testNextLong_DoubleNotExactLong() throws IOException {
    Reader reader = new StringReader("123.45");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.nextLong();
    jsonReader.close();
  }

  @Test(expected = IllegalStateException.class)
  public void testNextLong_NotANumberToken() throws IOException {
    Reader reader = new StringReader("[1,2,3]");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.beginArray();
    jsonReader.nextLong();
    jsonReader.close();
  }

  @Test(expected = IOException.class)
  public void testNextLong_UnexpectedEndOfFile() throws IOException {
    Reader reader = new StringReader("123");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.nextLong();
    jsonReader.nextLong(); // Should throw EOFException
    jsonReader.close();
  }

  @Test
  public void testNextLong_ArrayOfLongs() throws IOException {
    Reader reader = new StringReader("[1,2,3]");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.beginArray();
    assertEquals(1L, jsonReader.nextLong());
    assertEquals(2L, jsonReader.nextLong());
    assertEquals(3L, jsonReader.nextLong());
    jsonReader.endArray();
    jsonReader.close();
  }

  @Test
  public void testNextLong_ObjectWithLongs() throws IOException {
    Reader reader = new StringReader("{\"a\": 100, \"b\": 200}");
    JsonReader jsonReader = new JsonReader(reader);
    jsonReader.beginObject();
    assertEquals("a", jsonReader.nextName());
    assertEquals(100L, jsonReader.nextLong());
    assertEquals("b", jsonReader.nextName());
    assertEquals(200L, jsonReader.nextLong());
    jsonReader.endObject();
    jsonReader.close();
  }
}