package com.google.gson.internal.sql;

import com.google.gson.Gson;
import com.google.gson.JsonSyntaxException;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import com.google.gson.stream.JsonWriter;
import org.junit.Before;
import org.junit.Test;

import java.io.IOException;
import java.io.StringReader;
import java.sql.Date;
import java.text.DateFormat;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.TimeZone;

import static org.junit.Assert.*;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public class SqlDateTypeAdapter_read_Test {

    private SqlDateTypeAdapter adapter;
    private DateFormat dateFormat;

    @Before
    public void setUp() {
        adapter = new SqlDateTypeAdapter();
        dateFormat = new SimpleDateFormat("yyyy-MM-dd");
        dateFormat.setTimeZone(TimeZone.getTimeZone("UTC"));
    }

    @Test
    public void testRead_NullValue() throws IOException {
        JsonReader reader = new JsonReader(new StringReader("null"));
        reader.peek(); // Advance to NULL token

        Date result = adapter.read(reader);
        assertNull(result);
    }

    @Test
    public void testRead_ValidDateString() throws IOException, ParseException {
        String dateString = "2023-10-15";
        JsonReader reader = new JsonReader(new StringReader("\"" + dateString + "\""));
        reader.peek(); // Advance to STRING token

        Date result = adapter.read(reader);
        assertNotNull(result);
        assertEquals(dateString, new SimpleDateFormat("yyyy-MM-dd").format(result));
    }

    @Test
    public void testRead_InvalidDateString_ThrowsJsonSyntaxException() {
        String invalidDateString = "invalid-date";
        JsonReader reader = new JsonReader(new StringReader("\"" + invalidDateString + "\""));
        reader.peek(); // Advance to STRING token

        try {
            adapter.read(reader);
            fail("Expected JsonSyntaxException to be thrown");
        } catch (JsonSyntaxException e) {
            assertTrue(e.getMessage().contains("Failed parsing"));
            assertTrue(e.getMessage().contains(invalidDateString));
        } catch (IOException e) {
            fail("Expected JsonSyntaxException, but got IOException: " + e.getMessage());
        }
    }

    @Test
    public void testRead_EmptyDateString_ThrowsJsonSyntaxException() {
        String emptyDateString = "";
        JsonReader reader = new JsonReader(new StringReader("\"" + emptyDateString + "\""));
        reader.peek(); // Advance to STRING token

        try {
            adapter.read(reader);
            fail("Expected JsonSyntaxException to be thrown");
        } catch (JsonSyntaxException e) {
            assertTrue(e.getMessage().contains("Failed parsing"));
            assertTrue(e.getMessage().contains(emptyDateString));
        } catch (IOException e) {
            fail("Expected JsonSyntaxException, but got IOException: " + e.getMessage());
        }
    }

    @Test
    public void testRead_WithDifferentTimeZone() throws IOException, ParseException {
        String dateString = "2023-10-15";
        JsonReader reader = new JsonReader(new StringReader("\"" + dateString + "\""));
        reader.peek(); // Advance to STRING token

        // Change time zone to test that it's properly restored
        TimeZone originalTZ = dateFormat.getTimeZone();
        dateFormat.setTimeZone(TimeZone.getTimeZone("GMT+5"));
        try {
            Date result = adapter.read(reader);
            assertNotNull(result);
            assertEquals(dateString, new SimpleDateFormat("yyyy-MM-dd").format(result));
        } finally {
            dateFormat.setTimeZone(originalTZ);
        }
    }

    @Test
    public void testRead_WithSpecialDateFormat() throws IOException, ParseException {
        String dateString = "2023-12-31";
        JsonReader reader = new JsonReader(new StringReader("\"" + dateString + "\""));
        reader.peek(); // Advance to STRING token

        Date result = adapter.read(reader);
        assertNotNull(result);
        assertEquals(dateString, new SimpleDateFormat("yyyy-MM-dd").format(result));
    }

    @Test
    public void testRead_WithFutureDate() throws IOException, ParseException {
        String dateString = "2050-06-15";
        JsonReader reader = new JsonReader(new StringReader("\"" + dateString + "\""));
        reader.peek(); // Advance to STRING token

        Date result = adapter.read(reader);
        assertNotNull(result);
        assertEquals(dateString, new SimpleDateFormat("yyyy-MM-dd").format(result));
    }

    @Test
    public void testRead_WithPastDate() throws IOException, ParseException {
        String dateString = "1900-01-01";
        JsonReader reader = new JsonReader(new StringReader("\"" + dateString + "\""));
        reader.peek(); // Advance to STRING token

        Date result = adapter.read(reader);
        assertNotNull(result);
        assertEquals(dateString, new SimpleDateFormat("yyyy-MM-dd").format(result));
    }
}